begin;

set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(14);

select ok((select relrowsecurity from pg_catalog.pg_class
    where oid = 'public.accounts'::pg_catalog.regclass),
    'accounts has RLS enabled');
select ok((select relforcerowsecurity from pg_catalog.pg_class
    where oid = 'public.accounts'::pg_catalog.regclass),
    'accounts forces RLS even for its table owner');
select ok(exists (
    select 1 from pg_catalog.pg_constraint
    where conrelid = 'public.accounts'::pg_catalog.regclass
      and conname = 'accounts_organization_shape_check'),
    'organization shape constraint exists');
select ok(exists (
    select 1 from pg_catalog.pg_constraint
    where conrelid = 'public.accounts'::pg_catalog.regclass
      and conname = 'accounts_user_id_fkey'
      and confdeltype = 'c'),
    'Auth foreign key cascades');

insert into auth.users (id, aud, role, email) values
    ('d1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'org-one@example.test'),
    ('d1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'org-two@example.test');

select is((select account_type::text from public.accounts
    where user_id = 'd1000000-0000-4000-8000-000000000001'),
    'individual', 'new Auth users start as individual accounts');
select is((select organization_name from public.accounts
    where user_id = 'd1000000-0000-4000-8000-000000000001'),
    null::text, 'individual accounts have no organization name');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'd1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'd1000000-0000-4000-8000-000000000001')::text, true);

select is((select count(*)::integer from public.accounts), 1,
    'an authenticated user reads only their own account row');
select throws_ok(
    $q$update public.accounts
       set account_type = 'organization'
     where user_id = 'd1000000-0000-4000-8000-000000000001'$q$,
    '42501', null, 'direct account profile updates are denied');
select is((select account_type::text from public.create_organization_account('Acme')),
    'organization', 'the RPC upgrades the authenticated account');
select is((select organization_name from public.accounts
    where user_id = 'd1000000-0000-4000-8000-000000000001'),
    'Acme', 'the organization name is stored');
select throws_ok(
    $q$select public.create_organization_account('   ')$q$,
    '22023', null, 'a blank organization name is rejected');
select is((select organization_name from public.create_organization_account('Acme 2')),
    'Acme 2', 'the organization name can be updated idempotently');

select set_config('request.jwt.claim.sub', 'd1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'd1000000-0000-4000-8000-000000000002')::text, true);
select is((select count(*)::integer from public.accounts), 1,
    'a second user sees only their own account row');
select is((select account_type::text from public.accounts),
    'individual', 'the second user remains an individual account');

select * from finish();
rollback;
