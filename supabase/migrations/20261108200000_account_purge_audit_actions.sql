-- Distinct audit actions for the two terminal stages; the audit table has a
-- unique (request_id, action) guard and the scoped slice already records
-- purge_claimed/purge_effect/purge_verified.
alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_action_check;
alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_action_check
        check (action in (
            'delete', 'request_created', 'request_accepted', 'manifest_frozen',
            'purge_claimed', 'workset_frozen', 'purge_effect', 'purge_verified',
            'purge_failed', 'reconciliation_verified', 'reconciliation_failed',
            'data_deleted', 'workspace_deleted',
            'account_prepared', 'account_purged'
        ));

drop policy evidence_deletion_audit_insert_worker
    on public.evidence_deletion_audit;
create policy evidence_deletion_audit_insert_worker
    on public.evidence_deletion_audit
    for insert to account_deletion_worker
    with check (
        request_id is not null
        and actor_user_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = evidence_deletion_audit.request_id
        )
        and resource_type = 'deletion_request'
        and action in (
            'purge_claimed', 'purge_effect', 'purge_verified', 'purge_failed',
            'account_prepared', 'account_purged'
        )
        and result in ('success', 'failure')
    );

create function private.prepare_individual_account_purge_v2(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    owned_workspace_ids uuid[];
    snapshot_result jsonb;
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

    snapshot_result := private.purge_snapshot_catalog_for_account(p_request_id);
    queued := (snapshot_result ->> 'storage_cleanup_queued')::integer;
    deleted_snapshots := (snapshot_result ->> 'snapshots_deleted')::integer;

    delete from public.source_grants
     where source_grants.principal_id = actor;
    get diagnostics deleted_grants = row_count;

    delete from public.evidence_relationships
     where evidence_relationships.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_relationships = row_count;

    delete from public.evidence_records
     where evidence_records.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_evidence = row_count;

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
        actor, null, 'deletion_request', p_request_id, 'account_prepared', 'success',
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

create function private.mark_account_purged_v2(p_request_id uuid)
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
        actor, null, 'deletion_request', p_request_id, 'account_purged', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('terminal', 'purged')
    );

    return pg_catalog.jsonb_build_object('status', 'purged', 'subject_id', actor);
end;
$function$;

grant create on schema private to account_deletion_worker;


grant create on schema private to account_deletion_worker;

do $$
begin
    execute format('grant account_deletion_worker to %I with set true, inherit false', current_user);
end
$$;

alter function private.prepare_individual_account_purge_v2(uuid)
    owner to account_deletion_worker;
alter function private.mark_account_purged_v2(uuid)
    owner to account_deletion_worker;

do $$
begin
    execute format('revoke account_deletion_worker from %I', current_user);
end
$$;

grant account_deletion_worker to postgres with set true, inherit false;

revoke create on schema private from account_deletion_worker;

revoke all on function private.prepare_individual_account_purge_v2(uuid)
    from public, anon, authenticated, service_role;
revoke all on function private.mark_account_purged_v2(uuid)
    from public, anon, authenticated, service_role;
grant execute on function private.prepare_individual_account_purge_v2(uuid)
    to account_deletion_worker;
grant execute on function private.mark_account_purged_v2(uuid)
    to account_deletion_worker;
