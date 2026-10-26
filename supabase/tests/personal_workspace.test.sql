begin;

-- Supabase CLI uses a temporary login role for hosted tests. Run fixtures as
-- postgres, then switch explicitly to anon/authenticated for RLS assertions.
set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(29);

select ok(
    (select relrowsecurity
     from pg_class
     where oid = 'public.workspaces'::regclass),
    'RLS remains enabled on workspaces'
);
select ok(
    (select relforcerowsecurity
     from pg_class
     where oid = 'public.workspaces'::regclass),
    'RLS remains forced on workspaces'
);
select ok(
    (select relrowsecurity
     from pg_class
     where oid = 'public.workspace_memberships'::regclass),
    'RLS remains enabled on workspace memberships'
);
select ok(
    (select relforcerowsecurity
     from pg_class
     where oid = 'public.workspace_memberships'::regclass),
    'RLS remains forced on workspace memberships'
);

select ok(
    has_function_privilege(
        'authenticated',
        'public.ensure_personal_workspace()',
        'EXECUTE'
    ),
    'authenticated can call the personal-workspace bootstrap'
);
select ok(
    not has_function_privilege(
        'anon',
        'public.ensure_personal_workspace()',
        'EXECUTE'
    ),
    'anon cannot call the personal-workspace bootstrap'
);
select ok(
    not has_function_privilege(
        'service_role',
        'public.ensure_personal_workspace()',
        'EXECUTE'
    ),
    'the user-facing bootstrap does not grant service_role execution'
);
select ok(
    (select procedure.prosecdef
     from pg_proc as procedure
     where procedure.oid = 'public.ensure_personal_workspace()'::regprocedure),
    'the bootstrap is security definer'
);
select ok(
    (select procedure.proconfig @> array[
        'search_path=pg_catalog',
        'row_security=off'
     ]
     from pg_proc as procedure
     where procedure.oid = 'public.ensure_personal_workspace()'::regprocedure),
    'the bootstrap pins search_path and fails closed if row security applies'
);
select ok(
    to_regclass('public.workspaces_personal_owner_unique') is not null,
    'a unique index enforces one personal workspace per user'
);
select ok(
    not has_column_privilege(
        'authenticated', 'public.workspaces', 'personal_owner_id', 'SELECT'
    ),
    'authenticated cannot select the personal owner key'
);
select ok(
    has_column_privilege('authenticated', 'public.workspaces', 'id', 'SELECT')
    and has_column_privilege('authenticated', 'public.workspaces', 'name', 'SELECT')
    and has_column_privilege('authenticated', 'public.workspaces', 'created_at', 'SELECT'),
    'authenticated retains select on public workspace metadata columns'
);
select ok(
    (select role.rolsuper or role.rolbypassrls
     from pg_proc as procedure
     join pg_roles as role on role.oid = procedure.proowner
     where procedure.oid = 'public.ensure_personal_workspace()'::regprocedure),
    'the trusted function owner can write through FORCE RLS'
);

insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'a@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'b@example.test');

create temporary table personal_workspace_observations (
    label text primary key,
    workspace_id uuid,
    workspace_name text,
    user_id uuid,
    role text,
    row_count text
);
grant select, insert on personal_workspace_observations to authenticated;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
insert into pg_temp.personal_workspace_observations (
    label, workspace_id, workspace_name, user_id, role
)
select 'a_first', workspace_id, workspace_name, user_id, role
from public.ensure_personal_workspace();
insert into pg_temp.personal_workspace_observations (
    label, workspace_id, workspace_name, user_id, role
)
select 'a_repeat', workspace_id, workspace_name, user_id, role
from public.ensure_personal_workspace();
set local role postgres;

select is(
    (select role from personal_workspace_observations where label = 'a_first'),
    'owner',
    'A receives owner membership in the personal workspace derived from its JWT'
);
select is(
    (select user_id::text from personal_workspace_observations where label = 'a_first'),
    '10000000-0000-4000-8000-000000000001',
    'A cannot choose another user identity for provisioning'
);
select is(
    (select count(distinct workspace_id)::text
     from personal_workspace_observations
     where label in ('a_first', 'a_repeat')),
    '1',
    'repeated provisioning returns the same personal workspace'
);
select is(
    (select count(*)::text
     from public.workspaces
     where personal_owner_id = '10000000-0000-4000-8000-000000000001'),
    '1',
    'A has exactly one personal workspace'
);
select is(
    (select count(*)::text
     from public.workspace_memberships
     where user_id = '10000000-0000-4000-8000-000000000001'
       and workspace_id = (select workspace_id from personal_workspace_observations where label = 'a_first')
       and role = 'owner'
       and status = 'active'),
    '1',
    'A has one active owner membership in its personal workspace'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
select throws_ok(
    'select personal_owner_id from public.workspaces where id = (select workspace_id from pg_temp.personal_workspace_observations where label = ''a_first'')',
    '42501',
    null,
    'authenticated cannot read personal_owner_id even on a visible workspace'
);
set local role postgres;

update public.workspace_memberships
set status = 'inactive'
where workspace_id = (select workspace_id from personal_workspace_observations where label = 'a_first')
  and user_id = '10000000-0000-4000-8000-000000000001';
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
select throws_ok(
    'select * from public.ensure_personal_workspace()',
    'PT403',
    'Personal workspace membership unavailable',
    'a user cannot reactivate an inactive personal membership through bootstrap'
);
set local role postgres;
select is(
    (select status
     from public.workspace_memberships
     where workspace_id = (select workspace_id from personal_workspace_observations where label = 'a_first')
       and user_id = '10000000-0000-4000-8000-000000000001'),
    'inactive',
    'bootstrap leaves an administratively inactive personal membership unchanged'
);

delete from public.workspace_memberships
where workspace_id = (select workspace_id from personal_workspace_observations where label = 'a_first')
  and user_id = '10000000-0000-4000-8000-000000000001';
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
select throws_ok(
    'select * from public.ensure_personal_workspace()',
    'PT403',
    'Personal workspace membership unavailable',
    'a missing personal membership is not recreated by bootstrap'
);
set local role postgres;
select is(
    (select count(*)::text
     from public.workspace_memberships
     where workspace_id = (select workspace_id from personal_workspace_observations where label = 'a_first')
       and user_id = '10000000-0000-4000-8000-000000000001'),
    '0',
    'bootstrap leaves an absent personal membership absent'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}',
    true
);
insert into pg_temp.personal_workspace_observations (
    label, workspace_id, workspace_name, user_id, role
)
select 'b_first', workspace_id, workspace_name, user_id, role
from public.ensure_personal_workspace();
set local role postgres;

select is(
    (select role from personal_workspace_observations where label = 'b_first'),
    'owner',
    'B receives owner membership in its own personal workspace'
);
select is(
    (select user_id::text from personal_workspace_observations where label = 'b_first'),
    '20000000-0000-4000-8000-000000000002',
    'B identity is derived independently from B JWT claims'
);
select isnt(
    (select workspace_id::text from personal_workspace_observations where label = 'a_first'),
    (select workspace_id::text from personal_workspace_observations where label = 'b_first'),
    'A and B receive distinct personal workspaces'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
insert into pg_temp.personal_workspace_observations (label, row_count)
select 'a_sees_b_workspace', count(*)::text
from public.workspaces
where id = (select workspace_id from pg_temp.personal_workspace_observations where label = 'b_first');
insert into pg_temp.personal_workspace_observations (label, row_count)
select 'a_sees_b_membership', count(*)::text
from public.workspace_memberships
where workspace_id = (select workspace_id from pg_temp.personal_workspace_observations where label = 'b_first');

select is(
    (select row_count from personal_workspace_observations where label = 'a_sees_b_workspace'),
    '0',
    'A cannot read B personal workspace'
);
select is(
    (select row_count from personal_workspace_observations where label = 'a_sees_b_membership'),
    '0',
    'A cannot read B personal membership'
);

select set_config('request.jwt.claim.sub', '', true);
select set_config(
    'request.jwt.claims',
    '{"role":"authenticated","aud":"authenticated"}',
    true
);
select throws_ok(
    'select * from public.ensure_personal_workspace()',
    'PT401',
    'Authentication required',
    'authenticated requests without a subject are rejected'
);
set local role postgres;

select * from finish();
rollback;
