-- P4: freeze exact application-row work items and remove only principal-owned
-- access rows. Shared workspace content and owner memberships remain intact.

alter table public.evidence_deletion_audit
    drop constraint evidence_deletion_audit_action_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_action_check
        check (action in (
            'delete', 'request_created', 'request_accepted', 'manifest_frozen',
            'purge_claimed', 'workset_frozen', 'purge_effect', 'purge_verified',
            'purge_failed'
        ));

create table public.data_deletion_work_set (
    work_item_id uuid primary key default pg_catalog.gen_random_uuid(),
    request_id uuid not null references public.data_deletion_requests (request_id) on delete restrict,
    target_type text not null check (target_type in ('source_grant', 'workspace_membership')),
    workspace_id uuid not null,
    source_id uuid,
    principal_id uuid not null references auth.users (id) on delete restrict,
    frozen_at timestamptz not null default pg_catalog.clock_timestamp(),
    constraint data_deletion_work_set_target_shape_check check (
        (target_type = 'source_grant' and source_id is not null)
        or (target_type = 'workspace_membership' and source_id is null)
    ),
    constraint data_deletion_work_set_request_id_target_type_workspace_id_source_id_principal_id_key
        unique (request_id, target_type, workspace_id, source_id, principal_id)
);

create index data_deletion_work_set_request_idx
    on public.data_deletion_work_set (request_id, target_type, workspace_id, source_id);

alter table public.data_deletion_work_set enable row level security;
alter table public.data_deletion_work_set force row level security;
revoke all on table public.data_deletion_work_set
    from public, anon, authenticated, service_role;
grant select, insert on table public.data_deletion_work_set
    to account_deletion_worker;

create policy data_deletion_work_set_worker_select
    on public.data_deletion_work_set
    for select to account_deletion_worker
    using (
        request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
    );

create policy data_deletion_work_set_worker_insert
    on public.data_deletion_work_set
    for insert to account_deletion_worker
    with check (
        request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
        and principal_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = data_deletion_work_set.request_id
              and request.status = 'purge_pending'
        )
    );

grant select, delete on table public.workspace_memberships
    to account_deletion_worker;

create policy workspace_memberships_worker_select_member_rows
    on public.workspace_memberships
    for select to account_deletion_worker
    using (
        user_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
        and role = 'member'
    );

create policy workspace_memberships_worker_delete_member_rows
    on public.workspace_memberships
    for delete to account_deletion_worker
    using (
        user_id = (
            select request.scope_id
            from public.data_deletion_requests as request
            where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid
              and request.status in ('purge_pending', 'purge_failed')
        )
        and role = 'member'
    );

create policy evidence_deletion_audit_insert_worker_workset
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
        and action = 'workset_frozen'
        and result = 'success'
    );

create or replace function private.run_account_deletion_slice(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    claim uuid;
    source_work_items bigint := 0;
    membership_work_items bigint := 0;
    deleted_grants bigint := 0;
    deleted_memberships bigint := 0;
    remaining_grants bigint := 0;
    remaining_memberships bigint := 0;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);
    select * into request_row
    from public.data_deletion_requests
    where request_id = p_request_id
    for update;
    if not found or request_row.status <> 'accepted' then
        raise exception 'deletion request is not claimable' using errcode = '55000';
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

    insert into public.data_deletion_work_set (
        request_id, target_type, workspace_id, source_id, principal_id
    )
    select p_request_id, 'source_grant', grant_row.workspace_id,
           grant_row.source_id, actor
    from public.source_grants as grant_row
    where grant_row.principal_id = actor
    on conflict do nothing;
    get diagnostics source_work_items = row_count;

    insert into public.data_deletion_work_set (
        request_id, target_type, workspace_id, source_id, principal_id
    )
    select p_request_id, 'workspace_membership', membership.workspace_id,
           null, actor
    from public.workspace_memberships as membership
    where membership.user_id = actor
      and membership.role = 'member'
    on conflict do nothing;
    get diagnostics membership_work_items = row_count;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'purge_claimed', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('claim_id', claim)
    ), (
        actor, null, 'deletion_request', p_request_id, 'workset_frozen', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object(
            'source_grant_targets', source_work_items,
            'membership_targets', membership_work_items
        )
    );

    begin
        delete from public.source_grants as grant_row
        using public.data_deletion_work_set as item
        where item.request_id = p_request_id
          and item.target_type = 'source_grant'
          and item.workspace_id = grant_row.workspace_id
          and item.source_id = grant_row.source_id
          and item.principal_id = grant_row.principal_id;
        get diagnostics deleted_grants = row_count;

        delete from public.workspace_memberships as membership
        using public.data_deletion_work_set as item
        where item.request_id = p_request_id
          and item.target_type = 'workspace_membership'
          and item.workspace_id = membership.workspace_id
          and item.principal_id = membership.user_id
          and membership.role = 'member';
        get diagnostics deleted_memberships = row_count;

        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_effect', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'source_grants_deleted', deleted_grants,
                'memberships_deleted', deleted_memberships
            )
        );

        select count(*) into remaining_grants
        from public.source_grants as grant_row
        join public.data_deletion_work_set as item
          on item.request_id = p_request_id
         and item.target_type = 'source_grant'
         and item.workspace_id = grant_row.workspace_id
         and item.source_id = grant_row.source_id
         and item.principal_id = grant_row.principal_id;
        select count(*) into remaining_memberships
        from public.workspace_memberships as membership
        join public.data_deletion_work_set as item
          on item.request_id = p_request_id
         and item.target_type = 'workspace_membership'
         and item.workspace_id = membership.workspace_id
         and item.principal_id = membership.user_id
         and membership.role = 'member';
        if remaining_grants <> 0 or remaining_memberships <> 0 then
            raise exception 'application purge verification did not reach zero';
        end if;

        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_verified', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'remaining_source_grants', remaining_grants,
                'remaining_member_memberships', remaining_memberships
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
        'source_grants_deleted', deleted_grants,
        'member_memberships_deleted', deleted_memberships
    );
end;
$function$;

-- ALTER OWNER requires SET on the target role; grant temporarily to the migration actor.
do $$
begin
    execute format('grant account_deletion_worker to %I with set true, inherit false', current_user);
end
$$;

alter function private.run_account_deletion_slice(uuid)
    owner to account_deletion_worker;

do $$
begin
    execute format('revoke account_deletion_worker from %I', current_user);
end
$$;

comment on table public.data_deletion_work_set is
    'Immutable worker-owned type/id work items for the account application purge slice; no content bodies.';
