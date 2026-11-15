-- C3: shared-workspace deletion requires unanimous approval of every active
-- owner. Direct group-workspace deletion is closed; an approved request is
-- executed by the privileged deletion worker after a 24-hour withdrawal window.

create table if not exists private.workspace_deletion_requests (
    request_id uuid primary key default pg_catalog.gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    organization_id uuid not null references public.organizations (id) on delete cascade,
    requested_by uuid not null references auth.users (id) on delete cascade,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'cancelled', 'stale')),
    owner_set_hash text not null,
    owner_count integer not null check (owner_count >= 1),
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    approved_at timestamptz,
    execute_after timestamptz,
    cancelled_at timestamptz
);

create unique index workspace_deletion_requests_active_key
    on private.workspace_deletion_requests (workspace_id)
    where status in ('pending', 'approved');

create table if not exists private.workspace_deletion_owners (
    request_id uuid not null references private.workspace_deletion_requests (request_id) on delete cascade,
    owner_user_id uuid not null references auth.users (id) on delete cascade,
    primary key (request_id, owner_user_id)
);

create table if not exists private.workspace_deletion_approvals (
    request_id uuid not null references private.workspace_deletion_requests (request_id) on delete cascade,
    owner_user_id uuid not null references auth.users (id) on delete cascade,
    approved_at timestamptz not null default pg_catalog.clock_timestamp(),
    primary key (request_id, owner_user_id)
);

alter table private.workspace_deletion_requests enable row level security;
alter table private.workspace_deletion_requests force row level security;
alter table private.workspace_deletion_owners enable row level security;
alter table private.workspace_deletion_owners force row level security;
alter table private.workspace_deletion_approvals enable row level security;
alter table private.workspace_deletion_approvals force row level security;

revoke all on table private.workspace_deletion_requests
    from public, anon, authenticated, service_role;
revoke all on table private.workspace_deletion_owners
    from public, anon, authenticated, service_role;
revoke all on table private.workspace_deletion_approvals
    from public, anon, authenticated, service_role;

create or replace function private.workspace_owner_set(
    p_workspace_id uuid
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
    select pg_catalog.jsonb_build_object(
        'owner_count', count(*)::integer,
        'owner_hash', pg_catalog.md5(
            pg_catalog.string_agg(membership.user_id::text, ',' order by membership.user_id)
        )
    )
    from public.workspaces as workspace
    join public.organization_memberships as membership
      on membership.organization_id = workspace.organization_id
    where workspace.id = p_workspace_id
      and membership.role = 'admin'
      and membership.status = 'active';
$function$;

create or replace function public.request_workspace_deletion(
    p_workspace_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    workspace_row public.workspaces;
    owners jsonb;
    owner_count integer;
    owner_hash text;
    existing private.workspace_deletion_requests;
    request private.workspace_deletion_requests;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    select * into workspace_row
    from public.workspaces
    where id = p_workspace_id
    for update;
    if not found or workspace_row.organization_id is null then
        raise exception 'group workspace not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1 from public.organization_memberships
        where organization_id = workspace_row.organization_id
          and user_id = actor
          and role = 'admin'
          and status = 'active'
    ) then
        raise exception 'active organization admin required' using errcode = '42501';
    end if;

    select * into existing
    from private.workspace_deletion_requests
    where workspace_id = p_workspace_id
      and status in ('pending', 'approved')
    for update;
    if found then
        return pg_catalog.jsonb_build_object(
            'request_id', existing.request_id,
            'workspace_id', existing.workspace_id,
            'status', existing.status,
            'owner_count', existing.owner_count,
            'approved_at', existing.approved_at,
            'execute_after', existing.execute_after
        );
    end if;

    owners := private.workspace_owner_set(p_workspace_id);
    owner_count := (owners ->> 'owner_count')::integer;
    owner_hash := owners ->> 'owner_hash';
    if owner_count < 1 then
        raise exception 'workspace has no active owners' using errcode = '55000';
    end if;

    insert into private.workspace_deletion_requests (
        workspace_id, organization_id, requested_by, owner_set_hash, owner_count
    ) values (
        p_workspace_id, workspace_row.organization_id, actor, owner_hash, owner_count
    ) returning * into request;

    insert into private.workspace_deletion_owners (request_id, owner_user_id)
    select request.request_id, membership.user_id
    from public.organization_memberships as membership
    where membership.organization_id = workspace_row.organization_id
      and membership.role = 'admin'
      and membership.status = 'active';

    insert into private.workspace_deletion_approvals (request_id, owner_user_id)
    values (request.request_id, actor)
    on conflict do nothing;

    if owner_count = 1 then
        update private.workspace_deletion_requests
           set status = 'approved',
               approved_at = pg_catalog.clock_timestamp(),
               execute_after = pg_catalog.clock_timestamp() + interval '24 hours'
         where request_id = request.request_id
         returning * into request;
    end if;

    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id,
        'workspace_id', request.workspace_id,
        'status', request.status,
        'owner_count', request.owner_count,
        'approved_at', request.approved_at,
        'execute_after', request.execute_after
    );
end;
$function$;

create or replace function public.approve_workspace_deletion(
    p_request_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    request private.workspace_deletion_requests;
    owners jsonb;
    approvals integer;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    select * into request
    from private.workspace_deletion_requests
    where request_id = p_request_id
    for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    if request.status in ('cancelled', 'stale') then
        return pg_catalog.jsonb_build_object(
            'request_id', request.request_id, 'status', request.status
        );
    end if;
    if not exists (
        select 1 from private.workspace_deletion_owners
        where request_id = p_request_id and owner_user_id = actor
    ) or not exists (
        select 1 from public.organization_memberships
        where organization_id = request.organization_id
          and user_id = actor and role = 'admin' and status = 'active'
    ) then
        raise exception 'active organization admin in the request snapshot required'
            using errcode = '42501';
    end if;

    owners := private.workspace_owner_set(request.workspace_id);
    if (owners ->> 'owner_hash') is distinct from request.owner_set_hash
       or (owners ->> 'owner_count')::integer is distinct from request.owner_count then
        update private.workspace_deletion_requests
           set status = 'stale'
         where request_id = p_request_id
         returning * into request;
        return pg_catalog.jsonb_build_object(
            'request_id', request.request_id, 'status', request.status
        );
    end if;

    insert into private.workspace_deletion_approvals (request_id, owner_user_id)
    values (p_request_id, actor)
    on conflict do nothing;

    select count(*)::integer into approvals
    from private.workspace_deletion_approvals
    where request_id = p_request_id;

    if approvals >= request.owner_count and request.status = 'pending' then
        update private.workspace_deletion_requests
           set status = 'approved',
               approved_at = pg_catalog.clock_timestamp(),
               execute_after = pg_catalog.clock_timestamp() + interval '24 hours'
         where request_id = p_request_id
         returning * into request;
    end if;

    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id,
        'status', request.status,
        'owner_count', request.owner_count,
        'approved_count', approvals,
        'approved_at', request.approved_at,
        'execute_after', request.execute_after
    );
end;
$function$;

create or replace function public.withdraw_workspace_deletion_approval(
    p_request_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    request private.workspace_deletion_requests;
    approvals integer;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    select * into request
    from private.workspace_deletion_requests
    where request_id = p_request_id
    for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    if request.status in ('cancelled', 'stale') then
        return pg_catalog.jsonb_build_object(
            'request_id', request.request_id, 'status', request.status
        );
    end if;
    delete from private.workspace_deletion_approvals
    where request_id = p_request_id and owner_user_id = actor;
    select count(*)::integer into approvals
    from private.workspace_deletion_approvals
    where request_id = p_request_id;
    if request.status = 'approved' and approvals < request.owner_count then
        update private.workspace_deletion_requests
           set status = 'pending', approved_at = null, execute_after = null
         where request_id = p_request_id
         returning * into request;
    end if;
    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id,
        'status', request.status,
        'approved_count', approvals
    );
end;
$function$;

create or replace function public.cancel_workspace_deletion(
    p_request_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    request private.workspace_deletion_requests;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    select * into request
    from private.workspace_deletion_requests
    where request_id = p_request_id
    for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    if actor <> request.requested_by and not exists (
        select 1 from private.workspace_deletion_owners
        where request_id = p_request_id and owner_user_id = actor
    ) then
        raise exception 'request owner required' using errcode = '42501';
    end if;
    update private.workspace_deletion_requests
       set status = 'cancelled',
           cancelled_at = pg_catalog.clock_timestamp(),
           approved_at = null,
           execute_after = null
     where request_id = p_request_id
     returning * into request;
    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id, 'status', request.status
    );
end;
$function$;

create or replace function public.workspace_deletion_status(
    p_workspace_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    request private.workspace_deletion_requests;
    approvals integer;
    owners jsonb;
begin
    if actor is null or not exists (
        select 1
        from public.workspaces as workspace
        join public.organization_memberships as membership
          on membership.organization_id = workspace.organization_id
        where workspace.id = p_workspace_id
          and membership.user_id = actor
          and membership.role = 'admin'
          and membership.status = 'active'
    ) then
        raise exception 'active organization admin required' using errcode = '42501';
    end if;
    select * into request
    from private.workspace_deletion_requests
    where workspace_id = p_workspace_id
      and status in ('pending', 'approved')
    for update;
    if not found then
        return null;
    end if;
    select count(*)::integer into approvals
    from private.workspace_deletion_approvals
    where request_id = request.request_id;
    select coalesce(
        pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'user_id', owner.owner_user_id,
            'approved', approval.owner_user_id is not null
        ) order by owner.owner_user_id), '[]'::jsonb)
      into owners
    from private.workspace_deletion_owners as owner
    left join private.workspace_deletion_approvals as approval
      on approval.request_id = owner.request_id
     and approval.owner_user_id = owner.owner_user_id
    where owner.request_id = request.request_id;
    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id,
        'workspace_id', request.workspace_id,
        'status', request.status,
        'requested_by', request.requested_by,
        'owner_count', request.owner_count,
        'approved_count', approvals,
        'owners', owners,
        'created_at', request.created_at,
        'approved_at', request.approved_at,
        'execute_after', request.execute_after
    );
end;
$function$;

create or replace function private.execute_workspace_deletion(
    p_request_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request private.workspace_deletion_requests;
    owners jsonb;
    queued integer := 0;
    deleted_snapshots integer := 0;
begin
    select * into request
    from private.workspace_deletion_requests
    where request_id = p_request_id
    for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    if request.status <> 'approved'
       or request.execute_after is null
       or request.execute_after > pg_catalog.clock_timestamp() then
        return pg_catalog.jsonb_build_object(
            'request_id', request.request_id, 'status', request.status, 'executed', false
        );
    end if;
    owners := private.workspace_owner_set(request.workspace_id);
    if (owners ->> 'owner_hash') is distinct from request.owner_set_hash
       or (owners ->> 'owner_count')::integer is distinct from request.owner_count then
        update private.workspace_deletion_requests set status = 'stale'
         where request_id = p_request_id;
        return pg_catalog.jsonb_build_object(
            'request_id', request.request_id, 'status', 'stale', 'executed', false
        );
    end if;

    insert into public.data_deletion_storage_queue (workspace_id, object_key)
    select snapshot.workspace_id, snapshot.object_key
    from public.source_snapshots as snapshot
    where snapshot.workspace_id = request.workspace_id
    on conflict (workspace_id, object_key) do nothing;
    get diagnostics queued = row_count;

    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'on', true);
    delete from public.source_snapshots
     where workspace_id = request.workspace_id;
    get diagnostics deleted_snapshots = row_count;
    perform pg_catalog.set_config('evidence.allow_snapshot_delete', 'off', true);

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result, metadata
    ) values (
        request.requested_by, request.workspace_id, 'workspace',
        request.workspace_id, 'workspace_deleted', 'success',
        pg_catalog.jsonb_build_object(
            'request_id', request.request_id,
            'organization_id', request.organization_id,
            'owner_count', request.owner_count,
            'snapshots_deleted', deleted_snapshots,
            'storage_cleanup_queued', queued
        )
    );

    delete from public.workspaces where id = request.workspace_id;
    return pg_catalog.jsonb_build_object(
        'request_id', request.request_id,
        'workspace_id', request.workspace_id,
        'status', 'executed',
        'snapshots_deleted', deleted_snapshots,
        'storage_cleanup_queued', queued
    );
end;
$function$;

revoke all on function private.workspace_owner_set(uuid)
    from public, anon, authenticated, service_role;
revoke all on function private.execute_workspace_deletion(uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.request_workspace_deletion(uuid)
    from public, anon, service_role;
revoke all on function public.approve_workspace_deletion(uuid)
    from public, anon, service_role;
revoke all on function public.withdraw_workspace_deletion_approval(uuid)
    from public, anon, service_role;
revoke all on function public.cancel_workspace_deletion(uuid)
    from public, anon, service_role;
revoke all on function public.workspace_deletion_status(uuid)
    from public, anon, service_role;

grant execute on function public.request_workspace_deletion(uuid) to authenticated;
grant execute on function public.approve_workspace_deletion(uuid) to authenticated;
grant execute on function public.withdraw_workspace_deletion_approval(uuid) to authenticated;
grant execute on function public.cancel_workspace_deletion(uuid) to authenticated;
grant execute on function public.workspace_deletion_status(uuid) to authenticated;

-- Close the direct deletion path: shared-workspace deletion must go through the
-- unanimous approval workflow.
revoke execute on function public.delete_group_workspace(uuid) from authenticated;
