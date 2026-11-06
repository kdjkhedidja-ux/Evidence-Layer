-- P4b: purge evidence and relationships only in a principal-owned workspace
-- with exactly one active owner (the principal) and no other active members.
-- Owner memberships, workspace rows, snapshots, and Storage objects remain.

alter table public.data_deletion_work_set
    add column target_id uuid;

update public.data_deletion_work_set
set target_id = case when target_type = 'source_grant' then source_id else workspace_id end
where target_id is null;

alter table public.data_deletion_work_set
    alter column target_id set not null;

alter table public.data_deletion_work_set
    drop constraint data_deletion_work_set_target_shape_check,
    drop constraint data_deletion_work_set_request_id_target_type_workspace_id_source_id_principal_id_key;

alter table public.data_deletion_work_set
    add constraint data_deletion_work_set_target_shape_check check (
        (target_type = 'source_grant' and source_id is not null)
        or (target_type = 'workspace_membership' and source_id is null)
        or (target_type in ('evidence_record', 'relationship') and source_id is null)
    ),
    add constraint data_deletion_work_set_target_id_required
        check (target_id is not null);

create unique index data_deletion_work_set_request_target_key
    on public.data_deletion_work_set (
        request_id, target_type, workspace_id, source_id, principal_id, target_id
    );

create or replace function private.deletion_workspace_is_sole_owner(
    p_request_id uuid,
    p_workspace_id uuid
)
returns boolean
language sql
security definer
set search_path = ''
as $function$
    select exists (
        select 1
        from public.data_deletion_requests as request
        where request.request_id = p_request_id
          and request.status = 'purge_pending'
          and exists (
              select 1
              from public.workspace_memberships as owner_membership
              where owner_membership.workspace_id = p_workspace_id
                and owner_membership.user_id = request.scope_id
                and owner_membership.role = 'owner'
                and owner_membership.status = 'active'
          )
          and 1 = (
              select count(*)
              from public.workspace_memberships as owner_count
              where owner_count.workspace_id = p_workspace_id
                and owner_count.role = 'owner'
                and owner_count.status = 'active'
          )
          and 0 = (
              select count(*)
              from public.workspace_memberships as member_count
              where member_count.workspace_id = p_workspace_id
                and member_count.role = 'member'
                and member_count.status = 'active'
          )
    )
$function$;

revoke all on function private.deletion_workspace_is_sole_owner(uuid, uuid)
    from public, anon, authenticated, service_role, account_deletion_worker;

grant select on table public.workspace_memberships to account_deletion_worker;
grant select, delete on table public.evidence_records to account_deletion_worker;
grant select, delete on table public.evidence_relationships to account_deletion_worker;

create policy workspace_memberships_worker_select_eligible_workspace
    on public.workspace_memberships
    for select to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
    );

create policy evidence_records_worker_select_eligible_workspace
    on public.evidence_records
    for select to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
    );

create policy evidence_records_worker_delete_eligible_workspace
    on public.evidence_records
    for delete to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
    );

create policy evidence_relationships_worker_select_eligible_workspace
    on public.evidence_relationships
    for select to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
    );

create policy evidence_relationships_worker_delete_eligible_workspace
    on public.evidence_relationships
    for delete to account_deletion_worker
    using (
        private.deletion_workspace_is_sole_owner(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            workspace_id
        )
    );

create or replace function private.audit_evidence_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    if actor is null then
        select request.scope_id into actor
        from public.data_deletion_requests as request
        where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid;
    end if;
    if actor is null then
        raise exception 'deletion audit requires an authenticated actor'
            using errcode = '42501';
    end if;
    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result, metadata
    ) values (
        actor, old.workspace_id, 'evidence', old.id, 'delete', 'success',
        case when nullif(current_setting('app.deletion_request_id', true), '') is not null
            then pg_catalog.jsonb_build_object('worker_slice', true) else '{}'::jsonb end
    );
    return old;
end;
$function$;

create or replace function private.audit_relationship_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    if pg_catalog.pg_trigger_depth() > 1 then
        return old;
    end if;
    if actor is null then
        select request.scope_id into actor
        from public.data_deletion_requests as request
        where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid;
    end if;
    if actor is null then
        raise exception 'deletion audit requires an authenticated actor'
            using errcode = '42501';
    end if;
    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result, metadata
    ) values (
        actor, old.workspace_id, 'relationship', old.id, 'delete', 'success',
        pg_catalog.jsonb_build_object('relationship_type', old.relation_type)
    );
    return old;
end;
$function$;

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
    relationship_work_items bigint := 0;
    evidence_work_items bigint := 0;
    deleted_grants bigint := 0;
    deleted_memberships bigint := 0;
    deleted_relationships bigint := 0;
    deleted_evidence bigint := 0;
    remaining_targets bigint := 0;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);
    select * into request_row from public.data_deletion_requests
    where request_id = p_request_id for update;
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

    insert into public.data_deletion_work_set
        (request_id, target_type, workspace_id, source_id, principal_id, target_id)
    select p_request_id, 'source_grant', grant_row.workspace_id,
           grant_row.source_id, actor, grant_row.source_id
    from public.source_grants as grant_row where grant_row.principal_id = actor
    on conflict do nothing;
    get diagnostics source_work_items = row_count;

    insert into public.data_deletion_work_set
        (request_id, target_type, workspace_id, source_id, principal_id, target_id)
    select p_request_id, 'workspace_membership', membership.workspace_id,
           null, actor, membership.workspace_id
    from public.workspace_memberships as membership
    where membership.user_id = actor and membership.role = 'member'
    on conflict do nothing;
    get diagnostics membership_work_items = row_count;

    insert into public.data_deletion_work_set
        (request_id, target_type, workspace_id, source_id, principal_id, target_id)
    select p_request_id, 'relationship', relationship.workspace_id,
           null, actor, relationship.id
    from public.evidence_relationships as relationship
    where private.deletion_workspace_is_sole_owner(p_request_id, relationship.workspace_id)
    on conflict do nothing;
    get diagnostics relationship_work_items = row_count;

    insert into public.data_deletion_work_set
        (request_id, target_type, workspace_id, source_id, principal_id, target_id)
    select p_request_id, 'evidence_record', evidence.workspace_id,
           null, actor, evidence.id
    from public.evidence_records as evidence
    where private.deletion_workspace_is_sole_owner(p_request_id, evidence.workspace_id)
    on conflict do nothing;
    get diagnostics evidence_work_items = row_count;

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
            'membership_targets', membership_work_items,
            'relationship_targets', relationship_work_items,
            'evidence_targets', evidence_work_items
        )
    );

    begin
        delete from public.source_grants grant_row using public.data_deletion_work_set item
        where item.request_id = p_request_id and item.target_type = 'source_grant'
          and item.workspace_id = grant_row.workspace_id and item.source_id = grant_row.source_id
          and item.principal_id = grant_row.principal_id;
        get diagnostics deleted_grants = row_count;
        delete from public.workspace_memberships membership using public.data_deletion_work_set item
        where item.request_id = p_request_id and item.target_type = 'workspace_membership'
          and item.workspace_id = membership.workspace_id and item.principal_id = membership.user_id
          and membership.role = 'member';
        get diagnostics deleted_memberships = row_count;
        delete from public.evidence_relationships relationship using public.data_deletion_work_set item
        where item.request_id = p_request_id and item.target_type = 'relationship'
          and item.target_id = relationship.id;
        get diagnostics deleted_relationships = row_count;
        delete from public.evidence_records evidence using public.data_deletion_work_set item
        where item.request_id = p_request_id and item.target_type = 'evidence_record'
          and item.target_id = evidence.id;
        get diagnostics deleted_evidence = row_count;

        select count(*) into remaining_targets
        from public.data_deletion_work_set item
        where item.request_id = p_request_id and (
            (item.target_type = 'source_grant' and exists (
                select 1 from public.source_grants grant_row where grant_row.workspace_id = item.workspace_id
                and grant_row.source_id = item.source_id and grant_row.principal_id = item.principal_id))
            or (item.target_type = 'workspace_membership' and exists (
                select 1 from public.workspace_memberships membership_row where membership_row.workspace_id = item.workspace_id
                and membership_row.user_id = item.principal_id and membership_row.role = 'member'))
            or (item.target_type in ('relationship', 'evidence_record') and exists (
                select 1 from public.evidence_records evidence_row where evidence_row.id = item.target_id
                union all select 1 from public.evidence_relationships relationship_row where relationship_row.id = item.target_id))
        );
        if remaining_targets <> 0 then
            raise exception 'application ownership purge verification did not reach zero';
        end if;
        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_effect', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'source_grants_deleted', deleted_grants,
                'memberships_deleted', deleted_memberships,
                'relationships_deleted', deleted_relationships,
                'evidence_deleted', deleted_evidence
            )
        ), (
            actor, null, 'deletion_request', p_request_id, 'purge_verified', 'success',
            p_request_id, 'account', pg_catalog.jsonb_build_object('remaining_targets', remaining_targets)
        );
    exception when others then
        update public.data_deletion_requests
        set status = 'purge_failed', purge_error_category = 'verification_failed'
        where request_id = p_request_id;
        update public.account_lifecycle set state = 'purge_failed', request_id = p_request_id
        where subject_id = actor;
        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            actor, null, 'deletion_request', p_request_id, 'purge_failed', 'failure',
            p_request_id, 'account', pg_catalog.jsonb_build_object('failure_category', 'verification_failed')
        );
        return pg_catalog.jsonb_build_object('status', 'purge_failed', 'failure_category', 'verification_failed');
    end;
    return pg_catalog.jsonb_build_object(
        'status', 'purge_pending', 'verified', true,
        'source_grants_deleted', deleted_grants,
        'member_memberships_deleted', deleted_memberships,
        'relationships_deleted', deleted_relationships,
        'evidence_deleted', deleted_evidence
    );
end;
$function$;

grant create on schema private to account_deletion_worker;

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

revoke create on schema private from account_deletion_worker;

comment on function private.deletion_workspace_is_sole_owner(uuid, uuid) is
    'True only for a request principal who is the sole active owner and has no active member peers.';
