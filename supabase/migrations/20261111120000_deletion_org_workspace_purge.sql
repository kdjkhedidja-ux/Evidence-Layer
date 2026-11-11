-- Account purge must include group workspaces owned by the requester's
-- organization. Group workspaces grant their owner an `admin` membership, not
-- `owner`, so the previous sole-owner predicate skipped them and the
-- `source_snapshots.workspace_id` RESTRICT foreign key could block Auth
-- identity deletion. Keep the fix account-scoped: the organization owner's
-- deletion removes the organization's group workspaces and snapshots.

create function private.deletion_workspace_is_account_owned(
    p_request_id uuid,
    p_workspace_id uuid
)
returns boolean
language sql
security definer
set search_path = ''
set row_security = off
as $function$
    select exists (
        select 1
        from public.data_deletion_requests as request
        join public.workspaces as workspace
          on workspace.id = p_workspace_id
        where request.request_id = p_request_id
          and request.status in ('purge_pending', 'purge_failed')
          and (
              workspace.personal_owner_id = request.scope_id
              or private.deletion_workspace_is_sole_owner(
                  p_request_id, workspace.id
              )
              or exists (
                  select 1
                  from public.organizations as organization
                  where organization.id = workspace.organization_id
                    and organization.owner_user_id = request.scope_id
              )
          )
    )
$function$;

revoke all on function private.deletion_workspace_is_account_owned(uuid, uuid)
    from public, anon, authenticated, service_role, account_deletion_worker;
grant execute on function private.deletion_workspace_is_account_owned(uuid, uuid)
    to account_deletion_worker;

create or replace function private.purge_snapshot_catalog_for_account(
    p_request_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
    owned_workspace_ids uuid[];
    queued integer := 0;
    deleted_snapshots integer := 0;
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
     where private.deletion_workspace_is_account_owned(
         p_request_id, workspace.id
     );
    if owned_workspace_ids is null then
        owned_workspace_ids := '{}'::uuid[];
    end if;

    insert into public.data_deletion_storage_queue (workspace_id, object_key)
    select snapshot.workspace_id, snapshot.object_key
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = any(owned_workspace_ids)
    on conflict (workspace_id, object_key) do nothing;
    get diagnostics queued = row_count;

    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'on', true);
    delete from public.source_snapshots
     where source_snapshots.workspace_id = any(owned_workspace_ids);
    get diagnostics deleted_snapshots = row_count;
    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'off', true);

    return pg_catalog.jsonb_build_object(
        'storage_cleanup_queued', queued,
        'snapshots_deleted', deleted_snapshots
    );
end;
$function$;

create function private.prepare_individual_account_purge_v3(
    p_request_id uuid
)
returns jsonb
language plpgsql
security invoker
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
     where private.deletion_workspace_is_account_owned(
         p_request_id, workspace.id
     );
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

revoke all on function private.prepare_individual_account_purge_v3(uuid)
    from public, anon, authenticated, service_role, account_deletion_worker;
grant execute on function private.prepare_individual_account_purge_v3(uuid)
    to account_deletion_worker;

alter policy workspaces_worker_select_owned
    on public.workspaces
    using (
        private.deletion_workspace_is_account_owned(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            id
        )
    );

alter policy workspaces_worker_delete_owned
    on public.workspaces
    using (
        private.deletion_workspace_is_account_owned(
            nullif(current_setting('app.deletion_request_id', true), '')::uuid,
            id
        )
    );
