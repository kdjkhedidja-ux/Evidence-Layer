begin;

set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(44);

-- Schema and privilege shape -------------------------------------------------
select ok((select relrowsecurity from pg_catalog.pg_class
    where oid = 'public.account_lifecycle'::pg_catalog.regclass),
    'account_lifecycle has RLS enabled');
select ok((select relforcerowsecurity from pg_catalog.pg_class
    where oid = 'public.account_lifecycle'::pg_catalog.regclass),
    'account_lifecycle forces RLS even for its table owner');
select is((select pg_catalog.pg_get_constraintdef(oid)
    from pg_catalog.pg_constraint
    where conrelid = 'public.account_lifecycle'::pg_catalog.regclass
      and conname = 'account_lifecycle_state_check'),
    $expected$CHECK ((state = ANY (ARRAY['available'::text, 'revocation_pending'::text, 'revoked'::text, 'purge_pending'::text, 'purge_failed'::text, 'purged'::text])))$expected$,
    'the state constraint is exactly the six C0 lifecycle labels');
select ok((select confdeltype = 'r'
    from pg_catalog.pg_constraint
    where conrelid = 'public.account_lifecycle'::pg_catalog.regclass
      and conname = 'account_lifecycle_subject_id_fkey'),
    'the Auth foreign key uses ON DELETE RESTRICT');
select ok(exists (
    select 1 from pg_catalog.pg_constraint
    where conrelid = 'public.account_lifecycle'::pg_catalog.regclass
      and conname = 'account_lifecycle_row_version_check'
      and pg_catalog.pg_get_constraintdef(oid) like '%row_version > 0%'
    ),
    'row_version must remain positive');
select is((select count(*)::integer
    from information_schema.columns
    where table_schema = 'private'
      and table_name = 'account_lifecycle_self'
      and column_name in ('subject_id', 'state', 'row_version')),
    3, 'the self projection exposes only subject_id, state, and row_version');
select ok((select reloptions @> array['security_invoker=true']
    from pg_catalog.pg_class
    where oid = 'private.account_lifecycle_self'::pg_catalog.regclass),
    'the self projection runs with invoker privileges');
select is((select count(*)::integer
    from pg_catalog.pg_policies
    where schemaname = 'public' and tablename = 'account_lifecycle'),
    4, 'the table has only the self-read and minimal trusted-role policies');
select ok((select position('account_lifecycle' in coalesce(qual, '')) = 0
    from pg_catalog.pg_policies
    where schemaname = 'public' and tablename = 'account_lifecycle'
      and policyname = 'account_lifecycle_select_self'),
    'the self-row policy does not query account_lifecycle and cannot recurse');

select ok((select bool_and(
        not rolcanlogin and not rolinherit and not rolsuper
        and not rolbypassrls and not rolcreaterole and not rolcreatedb
        and not rolreplication
    )
    from pg_catalog.pg_roles
    where rolname in (
        'account_lifecycle_provisioner',
        'account_lifecycle_transition_writer'
    )),
    'internal lifecycle roles are non-login, non-inheriting, and non-privileged');
select ok(not exists (
    select 1
    from pg_catalog.pg_roles as api_role
    where api_role.rolname in (
        'anon', 'authenticated', 'service_role', 'authenticator',
        'supabase_auth_admin'
    )
    and (
        pg_catalog.pg_has_role(api_role.oid, 'account_lifecycle_provisioner', 'member')
        or pg_catalog.pg_has_role(api_role.oid, 'account_lifecycle_transition_writer', 'member')
    )),
    'no API or Auth role can assume an internal lifecycle role');
select ok(
    pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'subject_id', 'select')
    and pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'state', 'select')
    and pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'row_version', 'select')
    and not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'request_id', 'select')
    and not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'transition_actor', 'select')
    and not pg_catalog.has_column_privilege('authenticated', 'public.account_lifecycle', 'transition_reason', 'select'),
    'authenticated has column SELECT only for the approved lifecycle projection');
select ok(
    not pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'insert')
    and not pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'update')
    and not pg_catalog.has_table_privilege('authenticated', 'public.account_lifecycle', 'delete'),
    'authenticated has no lifecycle DML');
select ok(
    not pg_catalog.has_table_privilege('anon', 'public.account_lifecycle', 'select')
    and not pg_catalog.has_table_privilege('anon', 'public.account_lifecycle', 'insert')
    and not pg_catalog.has_table_privilege('anon', 'private.account_lifecycle_self', 'select'),
    'anon has no lifecycle access');
select ok(
    not pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'select')
    and not pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'insert')
    and not pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'update')
    and not pg_catalog.has_table_privilege('service_role', 'public.account_lifecycle', 'delete'),
    'service_role receives no lifecycle table privilege');
select ok(
    not pg_catalog.has_schema_privilege('account_lifecycle_provisioner', 'auth', 'usage')
    and not pg_catalog.has_table_privilege('account_lifecycle_provisioner', 'auth.users', 'select')
    and pg_catalog.has_column_privilege('account_lifecycle_provisioner', 'public.account_lifecycle', 'subject_id', 'insert')
    and not pg_catalog.has_column_privilege('account_lifecycle_provisioner', 'public.account_lifecycle', 'subject_id', 'select')
    and not pg_catalog.has_table_privilege('account_lifecycle_provisioner', 'public.account_lifecycle', 'select')
    and not pg_catalog.has_table_privilege('account_lifecycle_provisioner', 'public.account_lifecycle', 'update')
    and not pg_catalog.has_table_privilege('account_lifecycle_provisioner', 'public.account_lifecycle', 'delete'),
    'the provisioning role can insert lifecycle rows but cannot read Auth or lifecycle records');
select ok(
    not pg_catalog.has_table_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'insert')
    and not pg_catalog.has_table_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'delete')
    and pg_catalog.has_column_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'state', 'update')
    and pg_catalog.has_column_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'request_id', 'update')
    and not pg_catalog.has_column_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'row_version', 'update')
    and not pg_catalog.has_column_privilege('account_lifecycle_transition_writer', 'public.account_lifecycle', 'transition_actor', 'update'),
    'the transition role can update only state/request reference, never version or actor');
select is((select count(*)::integer
    from pg_catalog.pg_proc as function_row
    join pg_catalog.pg_namespace as function_schema
      on function_schema.oid = function_row.pronamespace
    where function_schema.nspname = 'private'
      and function_row.proname in (
          'provision_account_lifecycle_for_auth_user'
      )
      and function_row.prosecdef
      and function_row.proowner = 'account_lifecycle_provisioner'::pg_catalog.regrole
      and function_row.proconfig @> array['search_path=""']),
    1, 'the Auth trigger helper uses the dedicated definer and empty search_path');
select ok(
    not pg_catalog.has_function_privilege('anon', 'private.provision_account_lifecycle_for_auth_user()', 'execute')
    and not pg_catalog.has_function_privilege('authenticated', 'private.provision_account_lifecycle_for_auth_user()', 'execute')
    and not pg_catalog.has_function_privilege('service_role', 'private.provision_account_lifecycle_for_auth_user()', 'execute'),
    'no API role can call the Auth provisioning trigger helper');

-- Synthetic Auth identities --------------------------------------------------
insert into auth.users (id, aud, role, email)
values
    ('88000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'lifecycle-a@example.test'),
    ('88000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'lifecycle-b@example.test');

select is((select count(*)::integer
    from auth.users as users
    join public.account_lifecycle as lifecycle on lifecycle.subject_id = users.id
    where users.id in (
        '88000000-0000-4000-8000-000000000001',
        '88000000-0000-4000-8000-000000000002'
    )),
    2, 'each newly inserted Auth subject receives exactly one lifecycle row');
select is((select count(*)::integer
    from public.account_lifecycle
    where subject_id in (
        '88000000-0000-4000-8000-000000000001',
        '88000000-0000-4000-8000-000000000002'
    )
      and state = 'available'
      and row_version = 1
      and request_id is null
      and transition_actor = 'auth_provisioning'
      and transition_reason = 'auth_identity_created'),
    2, 'new Auth subjects begin available at version one with trusted metadata');

alter table public.account_lifecycle disable trigger account_lifecycle_version_transition;
create policy account_lifecycle_test_admin_update
    on public.account_lifecycle for update to postgres
    using (true) with check (true);
select throws_ok(
    $q$update public.account_lifecycle
       set state = 'not_a_state',
           request_id = '88000000-0000-4000-8000-000000000099'
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '23514',
    'new row for relation "account_lifecycle" violates check constraint "account_lifecycle_state_check"',
    'the closed state constraint rejects unknown values'
);
drop policy account_lifecycle_test_admin_update on public.account_lifecycle;
alter table public.account_lifecycle enable trigger account_lifecycle_version_transition;

select throws_ok(
    $q$delete from auth.users where id = '88000000-0000-4000-8000-000000000001'$q$,
    '23503',
    'update or delete on table "users" violates foreign key constraint "account_lifecycle_subject_id_fkey" on table "account_lifecycle"',
    'Auth deletion cannot cascade away the lifecycle deny marker'
);

set local role authenticated;
select pg_catalog.set_config('request.jwt.claim.sub', '88000000-0000-4000-8000-000000000001', true);
select pg_catalog.set_config(
    'request.jwt.claims',
    '{"sub":"88000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
select is((select count(*)::integer
    from private.account_lifecycle_self
    where subject_id = '88000000-0000-4000-8000-000000000001'),
    1, 'an authenticated user can read their own minimal status projection');
select is((select count(*)::integer
    from private.account_lifecycle_self
    where subject_id = '88000000-0000-4000-8000-000000000002'),
    0, 'an authenticated user cannot inspect another subject');
select throws_ok(
    $q$select request_id from public.account_lifecycle
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'internal request references are not readable by authenticated');
select throws_ok(
    $q$insert into public.account_lifecycle
       (subject_id, state, row_version, request_id, transition_actor, transition_reason)
       values ('88000000-0000-4000-8000-000000000001', 'available', 1, null,
               'auth_provisioning', 'auth_identity_created')$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'authenticated cannot insert or self-bootstrap lifecycle state');
select throws_ok(
    $q$update public.account_lifecycle set state = 'revocation_pending'
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'authenticated cannot update or reactivate lifecycle state');
select throws_ok(
    $q$delete from public.account_lifecycle
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'authenticated cannot delete a lifecycle marker');
reset role;

-- A trusted test-only transition uses the isolated internal role. The role is
-- never granted to any API identity and the C4-gated post-denial states remain
-- unavailable in B1.
grant account_lifecycle_transition_writer to postgres with set true, inherit false;
grant usage on schema extensions to account_lifecycle_transition_writer;
set local role account_lifecycle_transition_writer;
update public.account_lifecycle
set state = 'revocation_pending',
    request_id = '88000000-0000-4000-8000-000000000011'
where subject_id = '88000000-0000-4000-8000-000000000001';
select throws_ok(
    $q$update public.account_lifecycle set row_version = 99
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'the transition role cannot supply row_version');
select throws_ok(
    $q$update public.account_lifecycle
       set state = 'revoked'
       where subject_id = '88000000-0000-4000-8000-000000000001'$q$,
    '23514',
    'B1 permits only the initial available-to-revocation_pending transition',
    'B1 cannot assert revoked without later access-surface/C4 evidence');
select throws_ok(
    $q$update public.account_lifecycle
       set state = 'revocation_pending', request_id = null
       where subject_id = '88000000-0000-4000-8000-000000000002'$q$,
    '23514',
    'revocation requires a request reference',
    'the denial transition requires a request reference');
reset role;
revoke usage on schema extensions from account_lifecycle_transition_writer;
revoke account_lifecycle_transition_writer from postgres;

select is((select row_version from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000001'),
    2::bigint, 'the trusted state transition increments row_version atomically');
select ok((select state = 'revocation_pending'
        and request_id = '88000000-0000-4000-8000-000000000011'
        and transition_actor = 'account_lifecycle_transition_writer'
        and transition_reason = 'local_denial_committed'
        and row_version = 2
    from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000001'),
    'transition state, request, version, and server metadata commit together');
select is((select row_version from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000002'),
    1::bigint, 'rejected transitions leave state and row_version unchanged');

-- Remove one synthetic row to model a missing lifecycle record. This temporary
-- RLS disable is test-only and is rolled back with this test transaction.
alter table public.account_lifecycle disable row level security;
delete from public.account_lifecycle
where subject_id = '88000000-0000-4000-8000-000000000002';
alter table public.account_lifecycle enable row level security;
alter table public.account_lifecycle force row level security;

set local role authenticated;
select pg_catalog.set_config('request.jwt.claim.sub', '88000000-0000-4000-8000-000000000002', true);
select pg_catalog.set_config(
    'request.jwt.claims',
    '{"sub":"88000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}',
    true
);
select is((select count(*)::integer
    from private.account_lifecycle_self),
    0, 'a valid Auth subject with a missing lifecycle row has no self-status row');
select throws_ok(
    $q$insert into public.account_lifecycle
       (subject_id, state, row_version, request_id, transition_actor, transition_reason)
       values ('88000000-0000-4000-8000-000000000002', 'available', 1, null,
               'auth_provisioning', 'auth_identity_created')$q$,
    '42501',
    'permission denied for table account_lifecycle',
    'a missing lifecycle row cannot be created by its user');
reset role;

create temporary table account_lifecycle_backfill_metrics (inserted bigint not null);
create policy account_lifecycle_test_backfill_insert
    on public.account_lifecycle for insert to postgres
    with check (
        state = 'available' and row_version = 1 and request_id is null
        and transition_actor = 'existing_user_backfill'
        and transition_reason = 'existing_identity_backfill'
    );
create policy account_lifecycle_test_backfill_select
    on public.account_lifecycle for select to postgres
    using (true);
alter table public.account_lifecycle disable trigger account_lifecycle_validate_insert;
do $run_backfill$
declare inserted_count bigint;
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
    insert into pg_temp.account_lifecycle_backfill_metrics values (inserted_count);
end;
$run_backfill$;
alter table public.account_lifecycle enable trigger account_lifecycle_validate_insert;
drop policy account_lifecycle_test_backfill_insert on public.account_lifecycle;

select is((select inserted from account_lifecycle_backfill_metrics),
    1::bigint, 'the trusted backfill inserts exactly the missing row');
select ok((select state = 'available' and row_version = 1
        and transition_actor = 'existing_user_backfill'
        and transition_reason = 'existing_identity_backfill'
    from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000002'),
    'backfill creates the missing subject in the initial available state');
select ok((select state = 'revocation_pending'
        and request_id = '88000000-0000-4000-8000-000000000011'
        and row_version = 2
    from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000001'),
    'backfill does not reset an existing non-available state or request');
select is((select count(*)::integer
    from auth.users as users
    join public.account_lifecycle as lifecycle on lifecycle.subject_id = users.id
    where users.id in (
        '88000000-0000-4000-8000-000000000001',
        '88000000-0000-4000-8000-000000000002'
    )),
    2, 'backfill leaves exactly one lifecycle row per synthetic Auth subject');
drop policy account_lifecycle_test_backfill_select on public.account_lifecycle;

-- Removing the trusted INSERT policy makes the AFTER INSERT Auth trigger fail.
-- The Auth row and its lifecycle row must roll back together.
drop policy account_lifecycle_insert_provisioner on public.account_lifecycle;
select throws_ok(
    $q$insert into auth.users (id, aud, role, email)
       values ('88000000-0000-4000-8000-000000000003',
               'authenticated', 'authenticated', 'lifecycle-failed@example.test')$q$,
    '42501',
    'new row violates row-level security policy for table "account_lifecycle"',
    'Auth provisioning failure aborts identity creation');
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
select is((select count(*)::integer from auth.users
    where id = '88000000-0000-4000-8000-000000000003'),
    0, 'failed provisioning leaves no Auth identity that could sign in');
select is((select count(*)::integer from public.account_lifecycle
    where subject_id = '88000000-0000-4000-8000-000000000003'),
    0, 'failed provisioning leaves no lifecycle row or partial grant');

select * from finish();
rollback;
