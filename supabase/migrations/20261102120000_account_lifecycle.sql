-- C2-B / B1: additive Account Lifecycle table and trusted provisioning.
-- This migration does not install API account gates or business-table/Storage
-- policies. The private self projection is intentionally outside the exposed
-- PostgREST schemas in config.toml.

-- This project has no prior deletion-request or journal tables. If a future
-- rollout reaches a database with such records, this backfill must stop for
-- explicit reconciliation rather than marking unresolved subjects available.
do $history_preflight$
begin
    if pg_catalog.to_regclass('public.data_deletion_requests') is not null
       or pg_catalog.to_regclass('public.deletion_journal_outbox') is not null
       or pg_catalog.to_regclass('private.account_lifecycle_backfill_holds') is not null then
        raise exception 'account lifecycle backfill requires deletion/recovery history reconciliation before it can run'
            using errcode = '55000';
    end if;
end;
$history_preflight$;

do $roles$
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'account_lifecycle_provisioner'
    ) then
        execute 'create role account_lifecycle_provisioner nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;

    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'account_lifecycle_transition_writer'
    ) then
        execute 'create role account_lifecycle_transition_writer nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;
end;
$roles$;

select private.assert_snapshot_role_safe('account_lifecycle_provisioner');
select private.assert_snapshot_role_safe('account_lifecycle_transition_writer');

create table public.account_lifecycle (
    subject_id uuid primary key references auth.users (id) on delete restrict,
    state text not null default 'available',
    row_version bigint not null default 1,
    request_id uuid,
    transition_id uuid not null default pg_catalog.gen_random_uuid(),
    transitioned_at timestamptz not null default pg_catalog.statement_timestamp(),
    transition_actor text not null,
    transition_reason text not null,
    created_at timestamptz not null default pg_catalog.statement_timestamp(),
    constraint account_lifecycle_state_check check (
        state in (
            'available', 'revocation_pending', 'revoked',
            'purge_pending', 'purge_failed', 'purged'
        )
    ),
    constraint account_lifecycle_row_version_check check (row_version > 0),
    constraint account_lifecycle_request_reference_check check (
        (state = 'available' and request_id is null)
        or (state <> 'available' and request_id is not null)
    ),
    constraint account_lifecycle_transition_actor_check check (
        transition_actor in (
            'auth_provisioning',
            'existing_user_backfill',
            'account_lifecycle_transition_writer'
        )
    ),
    constraint account_lifecycle_transition_reason_check check (
        transition_reason in (
            'auth_identity_created',
            'existing_identity_backfill',
            'local_denial_committed',
            'access_surfaces_verified',
            'purge_authorized',
            'purge_verified',
            'purge_failed',
            'purge_retry'
        )
    ),
    constraint account_lifecycle_transition_id_key unique (transition_id)
);

comment on table public.account_lifecycle is
    'Authoritative fail-closed Account Lifecycle state; never stored in user-controlled JWT metadata.';
comment on column public.account_lifecycle.request_id is
    'Current trusted lifecycle request; null only for an initially available account. A later migration may add the FK to data_deletion_requests.';
comment on column public.account_lifecycle.row_version is
    'Server-incremented on every trusted state transition in the same row update; not a standalone distributed fencing token.';

alter table public.account_lifecycle enable row level security;
alter table public.account_lifecycle force row level security;
revoke all privileges on table public.account_lifecycle
    from public, anon, authenticated, service_role;

grant usage on schema private to account_lifecycle_provisioner;
grant usage on schema public to account_lifecycle_provisioner;
grant insert (
    subject_id, state, row_version, request_id,
    transition_actor, transition_reason
) on table public.account_lifecycle to account_lifecycle_provisioner;

create policy account_lifecycle_insert_provisioner
    on public.account_lifecycle
    for insert to account_lifecycle_provisioner
    with check (
        state = 'available'
        and row_version = 1
        and request_id is null
        and transition_actor = 'auth_provisioning'
        and transition_reason = 'auth_identity_created'
    );

grant usage on schema public to account_lifecycle_transition_writer;
grant select (subject_id, state, row_version, request_id)
    on table public.account_lifecycle to account_lifecycle_transition_writer;
grant update (state, request_id)
    on table public.account_lifecycle to account_lifecycle_transition_writer;

create policy account_lifecycle_transition_writer_select
    on public.account_lifecycle
    for select to account_lifecycle_transition_writer
    using (true);

create policy account_lifecycle_transition_writer_update
    on public.account_lifecycle
    for update to account_lifecycle_transition_writer
    using (true)
    with check (true);

create policy account_lifecycle_select_self
    on public.account_lifecycle
    for select to authenticated
    using (subject_id = (select auth.uid()));

grant select (subject_id, state, row_version)
    on table public.account_lifecycle to authenticated;

create view private.account_lifecycle_self
with (security_invoker = true)
as
select subject_id, state, row_version
from public.account_lifecycle;

grant usage on schema private to authenticated;
revoke all privileges on table private.account_lifecycle_self
    from public, anon, service_role;
grant select on table private.account_lifecycle_self to authenticated;

create function private.enforce_account_lifecycle_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if current_user <> 'account_lifecycle_provisioner' then
        raise exception 'account lifecycle rows require trusted provisioning'
            using errcode = '42501';
    end if;

    if new.state <> 'available'
       or new.row_version <> 1
       or new.request_id is not null
       or new.transition_actor <> 'auth_provisioning'
       or new.transition_reason <> 'auth_identity_created' then
        raise exception 'initial account lifecycle rows must be available and server-provisioned'
            using errcode = '23514';
    end if;

    return new;
end;
$function$;

create function private.enforce_account_lifecycle_transition()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if current_user <> 'account_lifecycle_transition_writer' then
        raise exception 'account lifecycle transitions require the trusted transition role'
            using errcode = '42501';
    end if;

    if new.subject_id is distinct from old.subject_id
       or new.row_version is distinct from old.row_version
       or new.transition_id is distinct from old.transition_id
       or new.transitioned_at is distinct from old.transitioned_at
       or new.transition_actor is distinct from old.transition_actor
       or new.transition_reason is distinct from old.transition_reason
       or new.created_at is distinct from old.created_at then
        raise exception 'account lifecycle identity and server-managed metadata cannot be supplied'
            using errcode = '42501';
    end if;

    if old.state <> 'available' or new.state <> 'revocation_pending' then
        raise exception 'B1 permits only the initial available-to-revocation_pending transition'
            using errcode = '23514';
    end if;

    if new.request_id is null then
        raise exception 'revocation requires a request reference'
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
    new.transition_reason := 'local_denial_committed';

    return new;
end;
$function$;

grant create on schema private to account_lifecycle_provisioner;

create function private.provision_account_lifecycle_for_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
    insert into public.account_lifecycle (
        subject_id, state, row_version, request_id,
        transition_actor, transition_reason
    ) values (
        new.id, 'available', 1, null,
        'auth_provisioning', 'auth_identity_created'
    );
    return new;
end;
$function$;

revoke all on function private.provision_account_lifecycle_for_auth_user()
    from public, anon, authenticated, service_role, authenticator, supabase_auth_admin;
-- Trigger creation checks EXECUTE as the role running the migration. Supabase may
-- run migrations as postgres or supabase_admin, so grant to the actor and to
-- either administrative role when present. API and Auth roles stay excluded.
do $$
begin
    execute format(
        'grant execute on function private.provision_account_lifecycle_for_auth_user() to %I',
        current_user
    );
    if exists (select 1 from pg_catalog.pg_roles where rolname = 'postgres') then
        execute 'grant execute on function private.provision_account_lifecycle_for_auth_user() to postgres';
    end if;
    if exists (select 1 from pg_catalog.pg_roles where rolname = 'supabase_admin') then
        execute 'grant execute on function private.provision_account_lifecycle_for_auth_user() to supabase_admin';
    end if;
end
$$;



-- The migration actor gets a temporary, row-limited path for the one-time
-- backfill. Both policies are removed before this migration commits.
create policy account_lifecycle_migration_backfill_insert
    on public.account_lifecycle
    for insert to postgres, supabase_admin
    with check (
        state = 'available'
        and row_version = 1
        and request_id is null
        and transition_actor = 'existing_user_backfill'
        and transition_reason = 'existing_identity_backfill'
    );
create policy account_lifecycle_migration_backfill_select
    on public.account_lifecycle
    for select to postgres, supabase_admin
    using (true);

do $backfill$
declare
    inserted_count bigint;
    auth_subject_count bigint;
    matched_subject_count bigint;
begin
    insert into public.account_lifecycle (
        subject_id, state, row_version, request_id,
        transition_actor, transition_reason
    )
    select users.id, 'available', 1, null,
           'existing_user_backfill', 'existing_identity_backfill'
    from auth.users as users
    on conflict (subject_id) do nothing;
    get diagnostics inserted_count = row_count;

    select count(*) into auth_subject_count from auth.users;
    select count(*) into matched_subject_count
    from auth.users as users
    join public.account_lifecycle as lifecycle
      on lifecycle.subject_id = users.id;

    if auth_subject_count <> matched_subject_count then
        raise exception 'account lifecycle backfill incomplete: Auth subjects %, matched lifecycle rows %',
            auth_subject_count, matched_subject_count
            using errcode = '23514';
    end if;

    raise notice 'account lifecycle backfill inserted %, verified % Auth subjects',
        inserted_count, auth_subject_count;
end;
$backfill$;

drop policy account_lifecycle_migration_backfill_insert on public.account_lifecycle;
drop policy account_lifecycle_migration_backfill_select on public.account_lifecycle;

create trigger account_lifecycle_validate_insert
    before insert on public.account_lifecycle
    for each row execute function private.enforce_account_lifecycle_insert();

create trigger account_lifecycle_version_transition
    before update on public.account_lifecycle
    for each row execute function private.enforce_account_lifecycle_transition();

create trigger account_lifecycle_after_auth_insert
    after insert on auth.users
    for each row execute function private.provision_account_lifecycle_for_auth_user();

revoke create on schema private from account_lifecycle_provisioner;

revoke all on function private.enforce_account_lifecycle_insert()
    from public, anon, authenticated, service_role, authenticator;
revoke all on function private.enforce_account_lifecycle_transition()
    from public, anon, authenticated, service_role, authenticator;

-- ALTER OWNER requires SET on the target role; grant temporarily to the migration actor.
do $$
begin
    execute format('grant account_lifecycle_provisioner to %I with set true, inherit false', current_user);
end
$$;

alter function private.provision_account_lifecycle_for_auth_user()
    owner to account_lifecycle_provisioner;

do $$
begin
    execute format('revoke account_lifecycle_provisioner from %I', current_user);
end
$$;

do $privilege_audit$
declare
    provisioner oid := 'account_lifecycle_provisioner'::pg_catalog.regrole;
    transition_writer oid := 'account_lifecycle_transition_writer'::pg_catalog.regrole;
begin
    if exists (
        select 1
        from pg_catalog.pg_roles as role_row
        where role_row.oid in (provisioner, transition_writer)
          and (
              role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
              or role_row.rolbypassrls or role_row.rolcreaterole
              or role_row.rolcreatedb or role_row.rolreplication
          )
    ) then
        raise exception 'account lifecycle internal role has unsafe attributes'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_roles as api_role
        where api_role.rolname in ('anon', 'authenticated', 'service_role', 'authenticator', 'supabase_auth_admin')
          and (
              pg_catalog.pg_has_role(api_role.oid, provisioner, 'member')
              or pg_catalog.pg_has_role(api_role.oid, transition_writer, 'member')
          )
    ) then
        raise exception 'an API/Auth role can assume an account lifecycle internal role'
            using errcode = '55000';
    end if;

    if not (
        select relrowsecurity and relforcerowsecurity
        from pg_catalog.pg_class
        where oid = 'public.account_lifecycle'::pg_catalog.regclass
    ) then
        raise exception 'account_lifecycle RLS must be enabled and forced'
            using errcode = '55000';
    end if;

    if pg_catalog.has_table_privilege('anon', 'public.account_lifecycle', 'select')
       or pg_catalog.has_table_privilege('anon', 'public.account_lifecycle', 'insert')
       or pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'insert')
       or pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'update')
       or pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'delete')
       or pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'select')
       or pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'insert')
       or pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'update')
       or pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'delete')
       or pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'request_id', 'select')
       or pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'transition_actor', 'select')
       or pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'transition_reason', 'select')
    then
        raise exception 'account lifecycle API/service role privileges exceed the approved minimum'
            using errcode = '55000';
    end if;

    if not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'subject_id', 'select')
       or not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'state', 'select')
       or not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'row_version', 'select')
       or not pg_catalog.has_table_privilege('authenticated', 'private.account_lifecycle_self', 'select')
    then
        raise exception 'authenticated self-status projection grants are incomplete'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_roles as api_role
        cross join pg_catalog.pg_proc as function_row
        join pg_catalog.pg_namespace as function_schema
          on function_schema.oid = function_row.pronamespace
        where api_role.rolname in ('anon', 'authenticated', 'service_role', 'authenticator', 'supabase_auth_admin')
          and function_schema.nspname = 'private'
          and function_row.proname = 'provision_account_lifecycle_for_auth_user'
          and pg_catalog.has_function_privilege(api_role.oid, function_row.oid, 'execute')
    ) then
        raise exception 'an API/Auth role can execute an account lifecycle provisioning helper'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_policies
        where schemaname = 'public' and tablename = 'account_lifecycle') <> 4 then
        raise exception 'account lifecycle policy inventory differs from the reviewed four-policy set'
            using errcode = '55000';
    end if;
end;
$privilege_audit$;
