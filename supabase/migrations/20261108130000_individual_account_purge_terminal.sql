-- Individual-account terminal purge: remove owned workspaces/snapshots, queue
-- Storage object keys, and permit the worker to mark the account `purged`
-- after Storage and Auth identity deletion succeed.
--
-- The two functions are owned by account_deletion_worker so the lifecycle
-- trigger's current_user check remains effective and the worker RLS policies
-- apply. Auth identity deletion happens outside Postgres through the Supabase
-- Admin API; this migration only permits the terminal database state.

grant select, delete on table public.workspace_memberships
    to account_deletion_worker;
grant select, delete on table public.workspaces
    to account_deletion_worker;
grant select, delete on table public.source_snapshots
    to account_deletion_worker;
grant select, insert, update, delete on table public.data_deletion_storage_queue
    to account_deletion_worker;

create policy workspaces_worker_select_owned
    on public.workspaces
    for select to account_deletion_worker
    using (
        personal_owner_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
        or private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            id
        )
    );

create policy workspaces_worker_delete_owned
    on public.workspaces
    for delete to account_deletion_worker
    using (
        personal_owner_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
        or private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            id
        )
    );

create policy workspace_memberships_worker_delete_subject
    on public.workspace_memberships
    for delete to account_deletion_worker
    using (
        user_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
    );

create policy source_snapshots_worker_select_owned
    on public.source_snapshots
    for select to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
        or workspace_id in (
            select workspace.id
            from public.workspaces as workspace
            where workspace.personal_owner_id = (
                select request.scope_id
                from public.data_deletion_requests as request
                where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
                  and request.status in ('purge_pending', 'purge_failed')
            )
        )
    );

create policy source_snapshots_worker_delete_owned
    on public.source_snapshots
    for delete to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
        or workspace_id in (
            select workspace.id
            from public.workspaces as workspace
            where workspace.personal_owner_id = (
                select request.scope_id
                from public.data_deletion_requests as request
                where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
                  and request.status in ('purge_pending', 'purge_failed')
            )
        )
    );

create policy data_deletion_storage_queue_worker_all
    on public.data_deletion_storage_queue
    for all to account_deletion_worker
    using (true)
    with check (true);

drop policy account_lifecycle_worker_update_bound_request
    on public.account_lifecycle;

create policy account_lifecycle_worker_update_bound_request
    on public.account_lifecycle
    for update to account_deletion_worker
    using (
        subject_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
        )
    )
    with check (
        request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
        and state in ('purge_pending', 'purge_failed', 'purged')
    );

create or replace function private.enforce_account_deletion_worker_transition()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if current_user <> 'account_deletion_worker' then
        raise exception 'worker lifecycle transitions require the dedicated worker role'
            using errcode = '42501';
    end if;
    if new.subject_id is distinct from old.subject_id
       or new.row_version is distinct from old.row_version
       or new.transition_id is distinct from old.transition_id
       or new.transitioned_at is distinct from old.transitioned_at
       or new.transition_actor is distinct from old.transition_actor
       or new.transition_reason is distinct from old.transition_reason
       or new.created_at is distinct from old.created_at then
        raise exception 'worker cannot supply lifecycle identity or metadata'
            using errcode = '42501';
    end if;
    if not (
        (old.state = 'revocation_pending' and new.state in ('purge_pending', 'purge_failed'))
        or (old.state = 'purge_pending' and new.state in ('purge_failed', 'purged'))
        or (old.state = 'purge_failed' and new.state = 'purge_pending')
    ) or new.request_id is null then
        raise exception 'worker lifecycle transition is not permitted'
            using errcode = '23514';
    end if;
    if old.state = 'purge_pending' and new.state = 'purge_pending' then
        raise exception 'worker cannot repeat a purge transition'
            using errcode = '23514';
    end if;
    if old.row_version = 9223372036854775807 then
        raise exception 'account lifecycle row_version exhausted'
            using errcode = '22003';
    end if;
    new.row_version := old.row_version + 1;
    new.transition_id := pg_catalog.gen_random_uuid();
    new.transitioned_at := pg_catalog.clock_timestamp();
    new.transition_actor := current_user;
    new.transition_reason := case
        when new.state = 'purge_failed' then 'purge_failed'
        when new.state = 'purged' then 'account_purged'
        else 'purge_started'
    end;
    return new;
end;
$function$;

drop trigger if exists account_lifecycle_worker_transition on public.account_lifecycle;

create trigger account_lifecycle_worker_transition
    before update on public.account_lifecycle
    for each row when (new.state in ('purge_pending', 'purge_failed', 'purged'))
    execute function private.enforce_account_deletion_worker_transition();

create function private.prepare_individual_account_purge(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    owned_workspace_ids uuid[];
    queued integer := 0;
    deleted_grants integer := 0;
    deleted_relationships integer := 0;
    deleted_evidence integer := 0;
    deleted_snapshots integer := 0;
    deleted_memberships integer := 0;
    deleted_workspaces integer := 0;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);

    select * into request_row
      from public.data_deletion_requests
     where request_id = p_request_id
     for update;
    if not found or request_row.status <> 'purge_pending' then
        raise exception 'deletion request is not purge-pending' using errcode = '55000';
    end if;
    actor := request_row.requester_user_id;

    select pg_catalog.array_agg(workspace.id)
      into owned_workspace_ids
      from public.workspaces as workspace
     where workspace.personal_owner_id = actor
        or private.deletion_workspace_is_sole_owner(p_request_id, workspace.id);
    if owned_workspace_ids is null then
        owned_workspace_ids := '{}'::uuid[];
    end if;

    insert into public.data_deletion_storage_queue (workspace_id, object_key)
    select snapshot.workspace_id, snapshot.object_key
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = any(owned_workspace_ids)
    on conflict (workspace_id, object_key) do nothing;
    get diagnostics queued = row_count;

    delete from public.source_grants
     where source_grants.principal_id = actor;
    get diagnostics deleted_grants = row_count;

    delete from public.evidence_relationships
     where evidence_relationships.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_relationships = row_count;

    delete from public.evidence_records
     where evidence_records.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_evidence = row_count;

    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'on', true);
    delete from public.source_snapshots
     where source_snapshots.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_snapshots = row_count;
    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'off', true);

    delete from public.workspace_memberships
     where workspace_memberships.user_id = actor;
    get diagnostics deleted_memberships = row_count;

    delete from public.workspaces
     where workspaces.id = any(owned_workspace_ids);
    get diagnostics deleted_workspaces = row_count;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'purge_verified', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object(
            'workspaces_deleted', deleted_workspaces,
            'memberships_deleted', deleted_memberships,
            'source_grants_deleted', deleted_grants,
            'relationships_deleted', deleted_relationships,
            'evidence_deleted', deleted_evidence,
            'snapshots_deleted', deleted_snapshots,
            'storage_cleanup_queued', queued
        )
    );

    return pg_catalog.jsonb_build_object(
        'status', 'prepared',
        'subject_id', actor,
        'workspaces_deleted', deleted_workspaces,
        'memberships_deleted', deleted_memberships,
        'source_grants_deleted', deleted_grants,
        'relationships_deleted', deleted_relationships,
        'evidence_deleted', deleted_evidence,
        'snapshots_deleted', deleted_snapshots,
        'storage_cleanup_queued', queued
    );
end;
$function$;

create function private.mark_account_purged(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);

    select * into request_row
      from public.data_deletion_requests
     where request_id = p_request_id
     for update;
    if not found or request_row.status <> 'purge_pending' then
        raise exception 'deletion request is not purge-pending' using errcode = '55000';
    end if;
    actor := request_row.requester_user_id;

    update public.data_deletion_requests
       set status = 'purged'
     where request_id = p_request_id;

    update public.account_lifecycle
       set state = 'purged', request_id = p_request_id
     where subject_id = actor;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'purge_verified', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('terminal', 'purged')
    );

    return pg_catalog.jsonb_build_object('status', 'purged', 'subject_id', actor);
end;
$function$;

grant create on schema private to account_deletion_worker;

do $$
begin
    execute format('grant account_deletion_worker to %I with set true, inherit false', current_user);
end
$$;

alter function private.prepare_individual_account_purge(uuid)
    owner to account_deletion_worker;
alter function private.mark_account_purged(uuid)
    owner to account_deletion_worker;

do $$
begin
    execute format('revoke account_deletion_worker from %I', current_user);
end
$$;

revoke create on schema private from account_deletion_worker;

revoke all on function private.prepare_individual_account_purge(uuid)
    from public, anon, authenticated, service_role;
revoke all on function private.mark_account_purged(uuid)
    from public, anon, authenticated, service_role;

grant execute on function private.prepare_individual_account_purge(uuid)
    to account_deletion_worker;
grant execute on function private.mark_account_purged(uuid)
    to account_deletion_worker;
