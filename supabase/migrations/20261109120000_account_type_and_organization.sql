-- Stage A: account type and individual -> organization provisioning.
-- Accounts are separate from lifecycle state so the profile can evolve without
-- widening the worker's lifecycle control surface.

create table public.accounts (
    user_id uuid primary key references auth.users (id) on delete cascade,
    account_type text not null default 'individual'
        check (account_type in ('individual', 'organization')),
    organization_name text,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    updated_at timestamptz not null default pg_catalog.clock_timestamp(),
    constraint accounts_organization_shape_check check (
        (account_type = 'individual' and organization_name is null)
        or (
            account_type = 'organization'
            and organization_name is not null
            and pg_catalog.length(pg_catalog.btrim(organization_name)) between 1 and 120
        )
    )
);

insert into public.accounts (user_id)
select identity.id
  from auth.users as identity
on conflict (user_id) do nothing;

alter table public.accounts enable row level security;
alter table public.accounts force row level security;
revoke all on table public.accounts
    from public, anon, authenticated, service_role;
grant select on table public.accounts to authenticated;

create policy accounts_select_self
    on public.accounts
    for select to authenticated
    using (user_id = auth.uid());

create function private.provision_account_type_for_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
begin
    insert into public.accounts (user_id)
    values (new.id)
    on conflict (user_id) do nothing;
    return new;
end;
$function$;

revoke all on function private.provision_account_type_for_auth_user()
    from public, anon, authenticated, service_role, authenticator, supabase_auth_admin;

do $$
begin
    execute format(
        'grant execute on function private.provision_account_type_for_auth_user() to %I',
        current_user
    );
    if exists (select 1 from pg_catalog.pg_roles where rolname = 'postgres') then
        execute 'grant execute on function private.provision_account_type_for_auth_user() to postgres';
    end if;
    if exists (select 1 from pg_catalog.pg_roles where rolname = 'supabase_admin') then
        execute 'grant execute on function private.provision_account_type_for_auth_user() to supabase_admin';
    end if;
end
$$;

create trigger accounts_after_auth_insert
    after insert on auth.users
    for each row execute function private.provision_account_type_for_auth_user();

create function public.create_organization_account(p_name text)
returns public.accounts
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    actor uuid := auth.uid();
    clean_name text := pg_catalog.btrim(p_name);
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

    insert into public.accounts (
        user_id, account_type, organization_name, updated_at
    ) values (
        actor, 'organization', clean_name, pg_catalog.clock_timestamp()
    )
    on conflict (user_id) do update
        set account_type = 'organization',
            organization_name = excluded.organization_name,
            updated_at = pg_catalog.clock_timestamp();

    select * into result
      from public.accounts
     where user_id = actor;
    return result;
end;
$function$;

revoke all on function public.create_organization_account(text)
    from public, anon, service_role;
grant execute on function public.create_organization_account(text)
    to authenticated;

comment on table public.accounts is
    'Individual or organization account profile. Account type changes go through create_organization_account.';
