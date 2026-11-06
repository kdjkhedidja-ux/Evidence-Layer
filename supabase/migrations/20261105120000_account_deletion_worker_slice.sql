-- P3: bounded account deletion worker slice.
-- Only accepted requests may be claimed. This migration does not delete
-- memberships, evidence, snapshots, Storage objects, or Auth identities.

do $role$
begin
    create role account_deletion_worker
        nologin noinherit nobypassrls;
exception when duplicate_object then
    null;
end
$role$;

alter table public.evidence_deletion_audit
    drop constraint evidence_deletion_audit_action_check;

alter table public.evidence_deletion_audit
    drop constraint evidence_deletion_audit_result_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_action_check
        check (action in (
            'delete', 'request_created', 'request_accepted', 'manifest_frozen',
            'purge_claimed', 'purge_effect', 'purge_verified', 'purge_failed'
    ));

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_result_check
        check (result in ('success', 'failure'));

alter table public.data_deletion_requests
    add column claim_id uuid,
    add column claimed_at timestamptz,
    add column purge_error_category text,
    add constraint data_deletion_requests_purge_error_category_check
        check (purge_error_category is null or purge_error_category in (
            'effect_failed', 'verification_failed', 'worker_error'
        ));

alter table public.data_deletion_requests
    drop constraint data_deletion_requests_status_check;

alter table public.data_deletion_requests
    add constraint data_deletion_requests_status_check
        check (status in (
            'approval_pending', 'journal_ack_pending', 'accepted',
            'purge_pending', 'purge_failed', 'purged'
        ));

grant usage on schema public to account_deletion_worker;
grant select on table public.data_deletion_requests to account_deletion_worker;
grant update (status, claim_id, claimed_at, purge_error_category)
    on table public.data_deletion_requests to account_deletion_worker;

create policy data_deletion_requests_worker_select_claimable
    on public.data_deletion_requests
    for select to account_deletion_worker
    using (status in ('accepted', 'purge_pending', 'purge_failed'));

create policy data_deletion_requests_worker_update_claim
    on public.data_deletion_requests
    for update to account_deletion_worker
    using (
        request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
    )
    with check (
        request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
        and status in ('purge_pending', 'purge_failed', 'purged')
    );

grant select, update (state, request_id)
    on table public.account_lifecycle to account_deletion_worker;

create policy account_lifecycle_worker_select_bound_request
    on public.account_lifecycle
    for select to account_deletion_worker
    using (
        subject_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
        )
    );

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
        and state in ('purge_pending', 'purge_failed')
    );

grant select, delete on table public.source_grants to account_deletion_worker;

create policy source_grants_select_worker_bound_request
    on public.source_grants
    for select to account_deletion_worker
    using (
        principal_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
    );

create policy source_grants_delete_worker_bound_request
    on public.source_grants
    for delete to account_deletion_worker
    using (
        principal_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
    );

grant insert on table public.evidence_deletion_audit
    to account_deletion_worker;

create or replace function private.enforce_account_lifecycle_transition()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    -- P3 has a separate trigger for the dedicated worker transition. The
    -- original B1 trigger must not reject that trigger's same-row update.
    if current_user = 'account_deletion_worker' then
        return new;
    end if;
    if current_user <> 'account_lifecycle_transition_writer' then
        raise exception 'account lifecycle transitions require the trusted transition role'
            using errcode = '42501';
    end if;
    if new.subject_id is distinct from old.subject_id
       or new.row_version is distinct from old.row_version
       or new.transition_id is distinct from old.transition_id
       or new.transitioned_at is distinct from old.transitioned_at
       or new.transition_actor is distinct from old.transition_actor
       or new.transition_reason is distinct from old.transition_reason
       or new.created_at is distinct from old.created_at then
        raise exception 'account lifecycle identity and server-managed metadata cannot be supplied'
            using errcode = '42501';
    end if;
    if old.state <> 'available' or new.state <> 'revocation_pending' then
        raise exception 'B1 permits only the initial available-to-revocation_pending transition'
            using errcode = '23514';
    end if;
    if new.request_id is null then
        raise exception 'revocation requires a request reference'
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
    new.transition_reason := 'local_denial_committed';
    return new;
end;
$function$;

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
        and action in ('purge_claimed', 'purge_effect', 'purge_verified', 'purge_failed')
        and result in ('success', 'failure')
    );

create function private.enforce_account_deletion_worker_transition()
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
    if old.state <> 'revocation_pending'
       or new.state not in ('purge_pending', 'purge_failed')
       or new.request_id is null then
        raise exception 'worker lifecycle transition is not permitted'
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
    new.transition_reason := case when new.state = 'purge_failed'
        then 'purge_failed' else 'purge_started' end;
    return new;
end;
$function$;

create trigger account_lifecycle_worker_transition
    before update on public.account_lifecycle
    for each row when (new.state in ('purge_pending', 'purge_failed'))
    execute function private.enforce_account_deletion_worker_transition();

create function private.run_account_deletion_slice(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    claim uuid;
    deleted_grants bigint := 0;
    remaining_grants bigint := 0;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);

    select * into request_row
    from public.data_deletion_requests
    where request_id = p_request_id
    for update;

    if not found or request_row.status <> 'accepted' then
        raise exception 'deletion request is not claimable'
            using errcode = '55000';
    end if;
    actor := request_row.requester_user_id;
    claim := pg_catalog.gen_random_uuid();

    update public.data_deletion_requests
    set status = 'purge_pending', claim_id = claim,
        claimed_at = pg_catalog.clock_timestamp(), purge_error_category = null
    where request_id = p_request_id;

    update public.account_lifecycle
    set state = 'purge_pending', request_id = p_request_id
    where subject_id = actor;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'purge_claimed', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('claim_id', claim)
    );

    begin
        delete from public.source_grants
        where principal_id = actor;
        get diagnostics deleted_grants = row_count;

        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_effect', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'target_type', 'source_grants', 'deleted_count', deleted_grants
            )
        );

        select count(*) into remaining_grants
        from public.source_grants
        where principal_id = actor;
        if remaining_grants <> 0 then
            raise exception 'source grant verification did not reach zero';
        end if;

        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_verified', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'target_type', 'source_grants', 'remaining_count', remaining_grants
            )
        );
    exception when others then
        update public.data_deletion_requests
        set status = 'purge_failed', purge_error_category = 'verification_failed'
        where request_id = p_request_id;
        update public.account_lifecycle
        set state = 'purge_failed', request_id = p_request_id
        where subject_id = actor;
        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_failed', 'failure',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'failure_category', 'verification_failed'
            )
        );
        return pg_catalog.jsonb_build_object(
            'status', 'purge_failed', 'failure_category', 'verification_failed'
        );
    end;

    return pg_catalog.jsonb_build_object(
        'status', 'purge_pending', 'verified', true,
        'target_type', 'source_grants', 'deleted_count', deleted_grants
    );
end;
$function$;


revoke all on function private.enforce_account_deletion_worker_transition()
    from public, anon, authenticated, service_role;
revoke all on function private.run_account_deletion_slice(uuid)
    from public, anon, authenticated, service_role;
grant execute on function private.run_account_deletion_slice(uuid)
    to account_deletion_worker;

comment on role account_deletion_worker is
    'NOLOGIN, NOINHERIT, NOBYPASSRLS role for the bounded accepted-account source-grant purge slice only.';

do $privilege_audit$
declare
    worker oid := 'account_deletion_worker'::pg_catalog.regrole;
begin
    if exists (
        select 1
        from pg_catalog.pg_roles as role_row
        where role_row.oid = worker
          and (role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
               or role_row.rolbypassrls or role_row.rolcreaterole
               or role_row.rolcreatedb or role_row.rolreplication)
    ) then
        raise exception 'account deletion worker has unsafe role attributes'
            using errcode = '55000';
    end if;
    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where membership.member = worker
    ) then
        raise exception 'account deletion worker must not inherit another role'
            using errcode = '55000';
    end if;
end
$privilege_audit$;
