-- Stage D: organization administrators can delete a group workspace and its
-- scoped content. Snapshot object keys are queued for the privileged worker.
create function public.delete_group_workspace(p_workspace_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    workspace_row public.workspaces;
    org_id uuid;
    object_keys jsonb;
    queued integer := 0;
    deleted_snapshots integer := 0;
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;

    select workspace.* into workspace_row
      from public.workspaces as workspace
     where workspace.id = p_workspace_id
       and workspace.organization_id is not null;
    if not found then
        raise exception 'Group workspace not found' using errcode = 'P0002';
    end if;

    select membership.organization_id into org_id
      from public.organization_memberships as membership
     where membership.user_id = actor
       and membership.organization_id = workspace_row.organization_id
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if org_id is null then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;

    select coalesce(jsonb_agg(snapshot.object_key order by snapshot.object_key), '[]'::jsonb)
      into object_keys
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = p_workspace_id;

    insert into public.data_deletion_storage_queue (workspace_id, object_key)
    select p_workspace_id, value
      from jsonb_array_elements_text(object_keys) as object_key
    on conflict (workspace_id, object_key) do nothing;
    get diagnostics queued = row_count;

    perform set_config('evidence.allow_snapshot_delete', 'on', true);
    delete from public.source_snapshots
     where source_snapshots.workspace_id = p_workspace_id;
    get diagnostics deleted_snapshots = row_count;
    perform set_config('evidence.allow_snapshot_delete', 'off', true);

    delete from public.workspaces where id = p_workspace_id;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result, metadata
    ) values (
        actor, p_workspace_id, 'workspace', p_workspace_id,
        'workspace_deleted', 'success',
        pg_catalog.jsonb_build_object(
            'organization_id', org_id,
            'snapshots_deleted', deleted_snapshots,
            'storage_cleanup_queued', queued
        )
    );

    return pg_catalog.jsonb_build_object(
        'workspace_id', p_workspace_id,
        'workspace_deleted', true,
        'snapshots_deleted', deleted_snapshots,
        'storage_cleanup_queued', queued
    );
end;
$function$;

revoke all on function public.delete_group_workspace(uuid)
    from public, anon, service_role;
grant execute on function public.delete_group_workspace(uuid) to authenticated;
