-- Stage B: organizations, organization memberships, and group workspaces.
-- The organization account owner is an admin member; group workspaces belong
-- to the organization and are visible through explicit memberships.

create table public.organizations (
    id uuid primary key default pg_catalog.gen_random_uuid(),
    name text not null check (pg_catalog.char_length(pg_catalog.btrim(name)) between 1 and 120),
    owner_user_id uuid not null references auth.users (id) on delete cascade,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    updated_at timestamptz not null default pg_catalog.clock_timestamp()
);

create unique index organizations_owner_user_unique
    on public.organizations (owner_user_id);

create table public.organization_memberships (
    organization_id uuid not null references public.organizations (id) on delete cascade,
    user_id uuid not null references auth.users (id) on delete cascade,
    role text not null check (role in ('admin', 'member')),
    status text not null default 'active' check (status in ('active', 'removed')),
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    updated_at timestamptz not null default pg_catalog.clock_timestamp(),
    primary key (organization_id, user_id)
);

create index organization_memberships_user_active_idx
    on public.organization_memberships (user_id, status, organization_id);

alter table public.organizations enable row level security;
alter table public.organizations force row level security;
alter table public.organization_memberships enable row level security;
alter table public.organization_memberships force row level security;
revoke all on table public.organizations, public.organization_memberships
    from public, anon, authenticated, service_role;
grant select on table public.organizations, public.organization_memberships
    to authenticated;

create policy organizations_select_member
    on public.organizations
    for select to authenticated
    using (
        owner_user_id = auth.uid()
        or exists (
            select 1
            from public.organization_memberships as membership
            where membership.organization_id = organizations.id
              and membership.user_id = auth.uid()
              and membership.status = 'active'
        )
    );

create policy organization_memberships_select_self
    on public.organization_memberships
    for select to authenticated
    using (user_id = auth.uid());

-- Link the organization account to its organization and backfill any existing
-- organization-type account created by Stage A.
alter table public.accounts
    add column organization_id uuid references public.organizations (id) on delete cascade;

insert into public.organizations (owner_user_id, name)
select account.user_id, account.organization_name
  from public.accounts as account
 where account.account_type = 'organization'
   and account.organization_name is not null
   and account.organization_id is null;

update public.accounts as account
   set organization_id = organization.id
  from public.organizations as organization
 where organization.owner_user_id = account.user_id
   and account.account_type = 'organization'
   and account.organization_id is null;

insert into public.organization_memberships (organization_id, user_id, role, status)
select organization.id, organization.owner_user_id, 'admin', 'active'
  from public.organizations as organization
on conflict (organization_id, user_id) do nothing;

alter table public.accounts
    drop constraint accounts_organization_shape_check;
alter table public.accounts
    add constraint accounts_organization_shape_check check (
        (account_type = 'individual' and organization_name is null and organization_id is null)
        or (
            account_type = 'organization'
            and organization_name is not null
            and organization_id is not null
        )
    );

-- Group workspaces: personal_owner_id stays null; organization_id is required.
alter table public.workspaces
    add column organization_id uuid references public.organizations (id) on delete cascade;
alter table public.workspaces
    add constraint workspaces_organization_shape_check check (
        (personal_owner_id is not null and organization_id is null)
        or (personal_owner_id is null and organization_id is not null)
    );

alter table public.workspace_memberships
    drop constraint workspace_memberships_role_check;
alter table public.workspace_memberships
    add constraint workspace_memberships_role_check
        check (role in ('owner', 'admin', 'member'));

-- Owned by the migration actor, so the Auth/owner checks remain definer-only.
create or replace function public.create_organization_account(p_name text)
returns public.accounts
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    clean_name text := pg_catalog.btrim(p_name);
    org_id uuid;
    result public.accounts;
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if clean_name is null
       or pg_catalog.length(clean_name) < 1
       or pg_catalog.length(clean_name) > 120 then
        raise exception 'Organization name must be 1-120 characters'
            using errcode = '22023';
    end if;

    select account.organization_id into org_id
      from public.accounts as account
     where account.user_id = actor;
    if org_id is null then
        insert into public.organizations (owner_user_id, name)
        values (actor, clean_name)
        returning id into org_id;
        update public.accounts
           set account_type = 'organization',
               organization_name = clean_name,
               organization_id = org_id,
               updated_at = pg_catalog.clock_timestamp()
         where user_id = actor;
    else
        update public.organizations
           set name = clean_name, updated_at = pg_catalog.clock_timestamp()
         where id = org_id;
        update public.accounts
           set account_type = 'organization',
               organization_name = clean_name,
               organization_id = org_id,
               updated_at = pg_catalog.clock_timestamp()
         where user_id = actor;
    end if;

    insert into public.organization_memberships (
        organization_id, user_id, role, status, updated_at
    ) values (
        org_id, actor, 'admin', 'active', pg_catalog.clock_timestamp()
    )
    on conflict (organization_id, user_id) do update
        set role = 'admin', status = 'active',
            updated_at = pg_catalog.clock_timestamp();

    select * into result from public.accounts where user_id = actor;
    return result;
end;
$function$;

create function public.organization_overview()
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    org public.organizations;
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    select organization.* into org
      from public.organizations as organization
      join public.organization_memberships as membership
        on membership.organization_id = organization.id
     where membership.user_id = actor
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if not found then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;

    return pg_catalog.jsonb_build_object(
        'id', org.id,
        'name', org.name,
        'owner_user_id', org.owner_user_id,
        'members', coalesce((
            select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'user_id', membership.user_id,
                'email', identity.email,
                'role', membership.role
            ) order by identity.email)
            from public.organization_memberships as membership
            join auth.users as identity on identity.id = membership.user_id
            where membership.organization_id = org.id
              and membership.status = 'active'
        ), '[]'::jsonb),
        'workspaces', coalesce((
            select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'id', workspace.id, 'name', workspace.name
            ) order by workspace.name)
            from public.workspaces as workspace
            where workspace.organization_id = org.id
        ), '[]'::jsonb)
    );
end;
$function$;

create function public.create_group_workspace(p_name text)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    org_id uuid;
    clean_name text := pg_catalog.btrim(p_name);
    workspace_id uuid;
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if clean_name is null
       or pg_catalog.length(clean_name) < 1
       or pg_catalog.length(clean_name) > 120 then
        raise exception 'Workspace name must be 1-120 characters'
            using errcode = '22023';
    end if;
    select membership.organization_id into org_id
      from public.organization_memberships as membership
     where membership.user_id = actor
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if org_id is null then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;

    insert into public.workspaces (name, organization_id)
    values (clean_name, org_id)
    returning id into workspace_id;

    insert into public.workspace_memberships (
        workspace_id, user_id, role, status
    )
    select workspace_id, membership.user_id,
           case when membership.role = 'admin' then 'admin' else 'member' end,
           'active'
      from public.organization_memberships as membership
     where membership.organization_id = org_id
       and membership.status = 'active'
    on conflict (workspace_id, user_id) do update
        set role = excluded.role, status = 'active';

    return pg_catalog.jsonb_build_object(
        'workspace_id', workspace_id, 'name', clean_name
    );
end;
$function$;

create function public.add_organization_member(p_email text, p_role text)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    org public.organizations;
    target_id uuid;
    clean_email text := pg_catalog.lower(pg_catalog.btrim(p_email));
    clean_role text := pg_catalog.lower(pg_catalog.btrim(p_role));
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if clean_role not in ('admin', 'member') then
        raise exception 'Role must be admin or member' using errcode = '22023';
    end if;
    select organization.* into org
      from public.organizations as organization
      join public.organization_memberships as membership
        on membership.organization_id = organization.id
     where membership.user_id = actor
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if not found then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;

    select identity.id into target_id
      from auth.users as identity
     where pg_catalog.lower(identity.email) = clean_email
     limit 1;
    if target_id is null then
        raise exception 'User not found' using errcode = 'P0002';
    end if;
    if target_id = org.owner_user_id and clean_role <> 'admin' then
        raise exception 'The organization owner must remain an admin'
            using errcode = '22023';
    end if;

    insert into public.organization_memberships (
        organization_id, user_id, role, status, updated_at
    ) values (
        org.id, target_id, clean_role, 'active', pg_catalog.clock_timestamp()
    )
    on conflict (organization_id, user_id) do update
        set role = excluded.role, status = 'active',
            updated_at = pg_catalog.clock_timestamp();

    insert into public.workspace_memberships (
        workspace_id, user_id, role, status
    )
    select workspace.id, target_id,
           case when clean_role = 'admin' then 'admin' else 'member' end,
           'active'
      from public.workspaces as workspace
     where workspace.organization_id = org.id
    on conflict (workspace_id, user_id) do update
        set role = excluded.role, status = 'active';

    return pg_catalog.jsonb_build_object(
        'user_id', target_id, 'email', clean_email, 'role', clean_role
    );
end;
$function$;

create function public.update_organization_member_role(p_user_id uuid, p_role text)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    org public.organizations;
    clean_role text := pg_catalog.lower(pg_catalog.btrim(p_role));
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if clean_role not in ('admin', 'member') then
        raise exception 'Role must be admin or member' using errcode = '22023';
    end if;
    select organization.* into org
      from public.organizations as organization
      join public.organization_memberships as membership
        on membership.organization_id = organization.id
     where membership.user_id = actor
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if not found then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;
    if p_user_id = org.owner_user_id and clean_role <> 'admin' then
        raise exception 'The organization owner must remain an admin'
            using errcode = '22023';
    end if;

    update public.organization_memberships
       set role = clean_role, status = 'active',
           updated_at = pg_catalog.clock_timestamp()
     where organization_id = org.id and user_id = p_user_id;
    if not found then
        raise exception 'Member not found' using errcode = 'P0002';
    end if;

    update public.workspace_memberships as membership
       set role = case when clean_role = 'admin' then 'admin' else 'member' end,
           status = 'active'
      from public.workspaces as workspace
     where membership.workspace_id = workspace.id
       and workspace.organization_id = org.id
       and membership.user_id = p_user_id;

    return pg_catalog.jsonb_build_object('user_id', p_user_id, 'role', clean_role);
end;
$function$;

create function public.remove_organization_member(p_user_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    org public.organizations;
begin
    if actor is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    select organization.* into org
      from public.organizations as organization
      join public.organization_memberships as membership
        on membership.organization_id = organization.id
     where membership.user_id = actor
       and membership.role = 'admin'
       and membership.status = 'active'
     limit 1;
    if not found then
        raise exception 'Organization admin required' using errcode = '42501';
    end if;
    if p_user_id = org.owner_user_id then
        raise exception 'The organization owner cannot be removed'
            using errcode = '22023';
    end if;

    delete from public.organization_memberships
     where organization_id = org.id and user_id = p_user_id;
    delete from public.workspace_memberships as membership
     using public.workspaces as workspace
     where membership.workspace_id = workspace.id
       and workspace.organization_id = org.id
       and membership.user_id = p_user_id;

    return pg_catalog.jsonb_build_object('user_id', p_user_id, 'removed', true);
end;
$function$;

revoke all on function public.organization_overview() from public, anon, service_role;
revoke all on function public.create_group_workspace(text) from public, anon, service_role;
revoke all on function public.add_organization_member(text, text) from public, anon, service_role;
revoke all on function public.update_organization_member_role(uuid, text) from public, anon, service_role;
revoke all on function public.remove_organization_member(uuid) from public, anon, service_role;

grant execute on function public.organization_overview() to authenticated;
grant execute on function public.create_group_workspace(text) to authenticated;
grant execute on function public.add_organization_member(text, text) to authenticated;
grant execute on function public.update_organization_member_role(uuid, text) to authenticated;
grant execute on function public.remove_organization_member(uuid) to authenticated;
