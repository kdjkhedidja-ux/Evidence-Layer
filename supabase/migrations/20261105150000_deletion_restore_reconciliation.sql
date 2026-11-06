-- P5: local reconciliation for restored rows against the immutable work set.
-- This never opens an account and does not replace an external journal/PITR proof.

alter table public.evidence_deletion_audit
    drop constraint evidence_deletion_audit_action_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_action_check
        check (action in (
            'delete', 'request_created', 'request_accepted', 'manifest_frozen',
            'purge_claimed', 'workset_frozen', 'purge_effect', 'purge_verified',
            'purge_failed', 'reconciliation_verified', 'reconciliation_failed'
        ));

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
    if old.state not in ('revocation_pending', 'purge_pending')
       or new.state not in ('purge_pending', 'purge_failed')
       or new.request_id is null then
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
    new.transition_reason := case when new.state = 'purge_failed'
        then 'purge_failed' else 'purge_started' end;
    return new;
end;
$function$;

create function private.reconcile_account_deletion_slice(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    remaining_targets bigint := 0;
begin
    perform pg_catalog.set_config('app.deletion_request_id', p_request_id::text, true);
    select * into request_row from public.data_deletion_requests
    where request_id = p_request_id for update;
    if not found or request_row.status <> 'purge_pending' then
        raise exception 'deletion request is not reconcilable'
            using errcode = '55000';
    end if;
    actor := request_row.requester_user_id;
    select count(*) into remaining_targets
    from public.data_deletion_work_set as item
    where item.request_id = p_request_id and (
        (item.target_type = 'source_grant' and exists (
            select 1 from public.source_grants grant_row
            where grant_row.workspace_id = item.workspace_id
              and grant_row.source_id = item.source_id
              and grant_row.principal_id = item.principal_id
        ))
        or (item.target_type = 'workspace_membership' and exists (
            select 1 from public.workspace_memberships membership_row
            where membership_row.workspace_id = item.workspace_id
              and membership_row.user_id = item.principal_id
              and membership_row.role = 'member'
        ))
        or (item.target_type = 'relationship' and exists (
            select 1 from public.evidence_relationships relationship_row
            where relationship_row.id = item.target_id
        ))
        or (item.target_type = 'evidence_record' and exists (
            select 1 from public.evidence_records evidence_row
            where evidence_row.id = item.target_id
        ))
    );
    if remaining_targets <> 0 then
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
            actor, null, 'deletion_request', p_request_id, 'reconciliation_failed', 'failure',
            p_request_id, 'account', pg_catalog.jsonb_build_object(
                'failure_category', 'restored_target_detected',
                'remaining_targets', remaining_targets
            )
        );
        return pg_catalog.jsonb_build_object(
            'status', 'purge_failed',
            'failure_category', 'restored_target_detected',
            'remaining_targets', remaining_targets
        );
    end if;
    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'reconciliation_verified', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('remaining_targets', 0)
    );
    return pg_catalog.jsonb_build_object(
        'status', 'purge_pending', 'reconciled', true, 'remaining_targets', 0
    );
end;
$function$;

-- ALTER OWNER requires SET on the target role; grant temporarily to the migration actor.
do $$
begin
    execute format('grant account_deletion_worker to %I with set true, inherit false', current_user);
end
$$;

alter function private.reconcile_account_deletion_slice(uuid)
    owner to account_deletion_worker;

do $$
begin
    execute format('revoke account_deletion_worker from %I', current_user);
end
$$;

revoke all on function private.reconcile_account_deletion_slice(uuid)
    from public, anon, authenticated, service_role;
grant execute on function private.reconcile_account_deletion_slice(uuid)
    to account_deletion_worker;

create policy evidence_deletion_audit_insert_worker_reconciliation
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
        and action in ('reconciliation_verified', 'reconciliation_failed')
        and result in ('success', 'failure')
    );
