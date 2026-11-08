-- Individual-account (personal workspace) data and workspace deletion.
--
-- Scope:
--   * The caller must be the personal owner of their single personal workspace.
--   * Data deletion removes workspace-scoped evidence, relationships, source
--     grants, and snapshot catalog rows, and returns the Storage object keys
--     that a privileged cleanup worker must delete.
--   * Workspace deletion performs the same data deletion, removes memberships,
--     and hard-deletes the workspace row.
--
-- Audit retention: evidence_deletion_audit keeps its rows. The workspace and
-- actor foreign keys become nullable with ON DELETE SET NULL so deletion is
-- possible without destroying the audit trail.

alter table public.evidence_deletion_audit
    alter column actor_user_id drop not null;

alter table public.evidence_deletion_audit
    alter column workspace_id drop not null;

alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_actor_user_id_fkey;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_actor_user_id_fkey
        foreign key (actor_user_id) references auth.users (id) on delete set null;

alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_workspace_id_fkey;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_workspace_id_fkey
        foreign key (workspace_id) references public.workspaces (id) on delete set null;

alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_resource_type_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_resource_type_check
        check (resource_type in ('evidence', 'relationship', 'workspace'));

alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_action_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_action_check
        check (action in (
            'delete', 'request_created', 'request_accepted', 'manifest_frozen',
            'purge_claimed', 'workset_frozen', 'purge_effect', 'purge_verified',
            'purge_failed', 'reconciliation_verified', 'reconciliation_failed',
            'data_deleted', 'workspace_deleted'
        ));

-- Storage cleanup queue. The deletion RPC enqueues exact object keys before it
-- removes the snapshot catalog rows. A privileged worker (direct Postgres
-- connection plus a Storage admin credential) drains this queue. API roles have
-- no access; the queue must not be exposed through PostgREST.
create table public.data_deletion_storage_queue (
    id uuid primary key default pg_catalog.gen_random_uuid(),
    workspace_id uuid not null,
    object_key text not null,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    completed_at timestamptz,
    attempts integer not null default 0 check (attempts >= 0),
    last_error text,
    constraint data_deletion_storage_queue_workspace_object_key
        unique (workspace_id, object_key)
);

alter table public.data_deletion_storage_queue enable row level security;
alter table public.data_deletion_storage_queue force row level security;
revoke all on table public.data_deletion_storage_queue
    from public, anon, authenticated, service_role;

-- The catalog remains immutable for ordinary callers. Only the controlled
-- deletion RPC below may set this transaction-local flag.
create or replace function private.reject_source_snapshot_delete()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if current_setting('evidence.allow_snapshot_delete', true) = 'on' then
        return old;
    end if;
    raise exception 'source snapshot catalog rows cannot be deleted'
        using errcode = '42501';
end;
$function$;

revoke all on function private.reject_source_snapshot_delete()
    from public, anon, authenticated, service_role;

create or replace function public.delete_personal_workspace_data()
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    v_user_id uuid := auth.uid();
    v_workspace_id uuid;
    v_object_keys jsonb;
    v_evidence integer := 0;
    v_relationships integer := 0;
    v_source_grants integer := 0;
    v_snapshots integer := 0;
    v_queued integer := 0;
begin
    if v_user_id is null then
        raise exception 'Authentication required'
            using errcode = 'PT401';
    end if;

    select workspace.id
      into v_workspace_id
      from public.workspaces as workspace
     where workspace.personal_owner_id = v_user_id;

    if v_workspace_id is null then
        raise exception 'Personal workspace unavailable'
            using errcode = 'PT404';
    end if;

    if not exists (
        select 1
        from public.workspace_memberships as membership
        where membership.workspace_id = v_workspace_id
          and membership.user_id = v_user_id
          and membership.role = 'owner'
          and membership.status = 'active'
    ) then
        raise exception 'Personal workspace ownership unavailable'
            using errcode = 'PT403';
    end if;

    select coalesce(jsonb_agg(snapshot.object_key order by snapshot.object_key), '[]'::jsonb)
      into v_object_keys
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = v_workspace_id;

    insert into public.data_deletion_storage_queue (workspace_id, object_key)
    select v_workspace_id, value
      from jsonb_array_elements_text(v_object_keys) as object_key
    on conflict (workspace_id, object_key) do nothing;
    get diagnostics v_queued = row_count;

    delete from public.source_grants
     where source_grants.workspace_id = v_workspace_id;
    get diagnostics v_source_grants = row_count;

    delete from public.evidence_relationships
     where evidence_relationships.workspace_id = v_workspace_id;
    get diagnostics v_relationships = row_count;

    delete from public.evidence_records
     where evidence_records.workspace_id = v_workspace_id;
    get diagnostics v_evidence = row_count;

    perform set_config('evidence.allow_snapshot_delete', 'on', true);
    delete from public.source_snapshots
     where source_snapshots.workspace_id = v_workspace_id;
    get diagnostics v_snapshots = row_count;
    perform set_config('evidence.allow_snapshot_delete', 'off', true);

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id,
        action, result, metadata
    ) values (
        v_user_id, v_workspace_id, 'workspace', v_workspace_id,
        'data_deleted', 'success',
        jsonb_build_object(
            'evidence_deleted', v_evidence,
            'relationships_deleted', v_relationships,
            'source_grants_deleted', v_source_grants,
            'snapshots_deleted', v_snapshots,
            'storage_object_count', jsonb_array_length(v_object_keys)
        )
    );

    return jsonb_build_object(
        'workspace_id', v_workspace_id,
        'evidence_deleted', v_evidence,
        'relationships_deleted', v_relationships,
        'source_grants_deleted', v_source_grants,
        'snapshots_deleted', v_snapshots,
        'storage_cleanup_queued', v_queued,
        'storage_cleanup_required', v_queued > 0
    );
end;
$function$;

create or replace function public.delete_personal_workspace()
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    v_user_id uuid := auth.uid();
    v_workspace_id uuid;
    v_result jsonb;
begin
    v_result := public.delete_personal_workspace_data();

    select workspace.id
      into v_workspace_id
      from public.workspaces as workspace
     where workspace.personal_owner_id = v_user_id;

    if v_workspace_id is null then
        raise exception 'Personal workspace unavailable'
            using errcode = 'PT404';
    end if;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id,
        action, result, metadata
    ) values (
        v_user_id, v_workspace_id, 'workspace', v_workspace_id,
        'workspace_deleted', 'success',
        jsonb_build_object('personal_owner', true)
    );

    delete from public.workspace_memberships
     where workspace_memberships.workspace_id = v_workspace_id;

    delete from public.workspaces
     where workspaces.id = v_workspace_id;

    return v_result || jsonb_build_object('workspace_deleted', true);
end;
$function$;

revoke all on function public.delete_personal_workspace_data()
    from public, anon, authenticated, service_role;
revoke all on function public.delete_personal_workspace()
    from public, anon, authenticated, service_role;
grant execute on function public.delete_personal_workspace_data()
    to authenticated;
grant execute on function public.delete_personal_workspace()
    to authenticated;

comment on function public.delete_personal_workspace_data() is
    'Deletes the caller personal-workspace content and snapshot catalog rows; returns Storage object keys for a privileged cleanup worker.';
comment on function public.delete_personal_workspace() is
    'Deletes the caller personal workspace and its content. Audit rows are retained with a null workspace reference.';
