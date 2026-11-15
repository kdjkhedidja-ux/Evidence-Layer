begin;

set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(21);

insert into auth.users (id, aud, role, email) values
    ('e1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'org-admin@example.test'),
    ('e1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'org-member@example.test'),
    ('e1000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'org-outsider@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);

select is((select organization_name from public.create_organization_account('Acme')),
    'Acme', 'the admin creates an organization');
select is((select role from public.organization_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000001'),
    'admin', 'the organization owner is an admin member');
select is((select count(*)::integer from public.organizations), 1,
    'the organization account owns one organization');
select (public.create_group_workspace('Group workspace') ->> 'workspace_id')::uuid
    as group_workspace_id \gset
select is((select name from public.workspaces
    where id = :'group_workspace_id'::uuid),
    'Group workspace', 'the admin creates a group workspace');
select is(jsonb_array_length(public.organization_overview() -> 'workspaces'), 1,
    'the group workspace belongs to the organization');
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000001'),
    'admin', 'the admin has an admin workspace membership');
select is((public.add_organization_member('org-member@example.test', 'member') ->> 'role'),
    'member', 'the admin adds a member');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000002')::text, true);
select is((select role from public.organization_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'member', 'the member is in the organization');
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'member', 'the member has a workspace membership');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);
select is(jsonb_array_length(public.organization_overview() -> 'members'), 2,
    'the overview lists the admin and the member');

select throws_ok(
    $q$select public.remove_organization_member('e1000000-0000-4000-8000-000000000001'::uuid)$q$,
    '22023', null, 'the organization owner cannot be removed');
select is((public.update_organization_member_role(
    'e1000000-0000-4000-8000-000000000002'::uuid, 'admin') ->> 'role'),
    'admin', 'the admin promotes the member');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000002')::text, true);
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'admin', 'the promoted member gets the admin workspace role');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);
select (public.request_workspace_deletion(:'group_workspace_id'::uuid) ->> 'request_id') as c3_request_id
\gset
select is((public.request_workspace_deletion(:'group_workspace_id'::uuid) ->> 'status'), 'pending',
    'the first admin opens a pending deletion request');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000002')::text, true);
select is((public.approve_workspace_deletion(:'c3_request_id'::uuid) ->> 'status'), 'approved',
    'the second admin completes unanimous approval');

set local role postgres;
update private.workspace_deletion_requests
set execute_after = clock_timestamp() - interval '1 second'
where request_id = :'c3_request_id'::uuid;
select is((private.execute_workspace_deletion(:'c3_request_id'::uuid) ->> 'status'), 'executed',
    'the approved workspace deletion executes');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);
select is((select count(*)::integer from public.workspaces
    where id = :'group_workspace_id'::uuid), 0,
    'the group workspace is removed');
select is((select count(*)::integer from public.workspace_memberships
    where workspace_id = :'group_workspace_id'::uuid), 0,
    'the group workspace memberships are removed');
select is(jsonb_array_length(public.organization_overview() -> 'workspaces'), 0,
    'the overview no longer lists the group workspace');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000003')::text, true);
select is((select count(*)::integer from public.organizations), 0,
    'an outsider cannot read the organization');
select is((select count(*)::integer from public.organization_memberships), 0,
    'an outsider cannot read organization memberships');

select * from finish();
rollback;
