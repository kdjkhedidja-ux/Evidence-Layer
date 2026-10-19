-- Provision exactly one personal workspace for the authenticated Supabase user.
-- The caller identity is taken only from auth.uid(); no user/workspace ID is accepted.

alter table public.workspaces
    add column personal_owner_id uuid
    references auth.users (id) on delete cascade;

-- Replace the workspace-wide SELECT grant with explicit public metadata only.
-- The owner key is used by the definer RPC and is not part of the client API.
revoke select on table public.workspaces from authenticated;
revoke select (personal_owner_id) on table public.workspaces from authenticated;
grant select (id, name, created_at) on table public.workspaces to authenticated;

create unique index workspaces_personal_owner_unique
    on public.workspaces (personal_owner_id)
    where personal_owner_id is not null;

create function public.ensure_personal_workspace()
returns table (
    workspace_id uuid,
    workspace_name text,
    user_id uuid,
    role text
)
language plpgsql
security definer
set search_path = pg_catalog
set row_security = off
as $$
declare
    v_user_id uuid := auth.uid();
    v_workspace_id uuid;
begin
    if v_user_id is null then
        raise exception 'Authentication required'
            using errcode = 'PT401';
    end if;

    insert into public.workspaces (name, personal_owner_id)
    values ('Personal workspace', v_user_id)
    on conflict (personal_owner_id) where personal_owner_id is not null
    do nothing
    returning id into v_workspace_id;

    if v_workspace_id is null then
        select workspace.id
        into v_workspace_id
        from public.workspaces as workspace
        where workspace.personal_owner_id = v_user_id;

        if v_workspace_id is null or not exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = v_workspace_id
              and membership.user_id = v_user_id
              and membership.role = 'owner'
              and membership.status = 'active'
        ) then
            raise exception 'Personal workspace membership unavailable'
                using errcode = 'PT403';
        end if;
    else
        insert into public.workspace_memberships (
            workspace_id,
            user_id,
            role,
            status
        )
        values (v_workspace_id, v_user_id, 'owner', 'active');
    end if;

    return query
    select workspace.id, workspace.name, v_user_id, 'owner'::text
    from public.workspaces as workspace
    where workspace.id = v_workspace_id
      and workspace.personal_owner_id = v_user_id;
end;
$$;

revoke all on function public.ensure_personal_workspace()
    from public, anon, authenticated, service_role;
grant execute on function public.ensure_personal_workspace()
    to authenticated;

comment on column public.workspaces.personal_owner_id is
    'Non-null only for the single personal workspace provisioned for that Supabase Auth user.';
comment on function public.ensure_personal_workspace() is
    'Provisions the caller personal workspace using auth.uid(); accepts no identity or workspace arguments and never restores an inactive membership.';
