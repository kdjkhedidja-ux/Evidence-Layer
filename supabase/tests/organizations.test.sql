begin;

set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(15);

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
select is((public.create_group_workspace('Group workspace') ->> 'name'),
    'Group workspace', 'the admin creates a group workspace');
select is(jsonb_array_length(public.organization_overview() -> 'workspaces'), 1,
    'the group workspace belongs to the organization');
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000001'),
    'admin', 'the admin has an admin workspace membership');
select is((public.add_organization_member('org-member@example.test', 'member') ->> 'role'),
    'member', 'the admin adds a member');
select is((select role from public.organization_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'member', 'the member is in the organization');
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'member', 'the member has a workspace membership');
select is(jsonb_array_length(public.organization_overview() -> 'members'), 2,
    'the overview lists the admin and the member');

select throws_ok(
    $q$select public.remove_organization_member('e1000000-0000-4000-8000-000000000001'::uuid)$q$,
    '22023', null, 'the organization owner cannot be removed');
select is((public.update_organization_member_role(
    'e1000000-0000-4000-8000-000000000002'::uuid, 'admin') ->> 'role'),
    'admin', 'the admin promotes the member');
select is((select role from public.workspace_memberships
    where user_id = 'e1000000-0000-4000-8000-000000000002'),
    'admin', 'the promoted member gets the admin workspace role');

select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000003')::text, true);
select is((select count(*)::integer from public.organizations), 0,
    'an outsider cannot read the organization');
select is((select count(*)::integer from public.organization_memberships), 0,
    'an outsider cannot read organization memberships');

select * from finish();
rollback;
