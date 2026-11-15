begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(9);

insert into auth.users (id, aud, role, email) values
    ('e2000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'c3-admin-a@example.test'),
    ('e2000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'c3-admin-b@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e2000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e2000000-0000-4000-8000-000000000001')::text, true);
select public.create_organization_account('C3 Org');
select (public.create_group_workspace('C3 Group') ->> 'workspace_id')::uuid as c3_workspace_id \gset
select public.add_organization_member('c3-admin-b@example.test', 'member');
select public.update_organization_member_role(
    'e2000000-0000-4000-8000-000000000002', 'admin'
);

create temp table c3_request as
select public.request_workspace_deletion(:'c3_workspace_id'::uuid) as payload;
select is((select payload ->> 'status' from c3_request), 'pending',
        'a two-admin workspace request starts pending');
select is((select (payload ->> 'owner_count')::integer from c3_request), 2,
        'the request snapshots two active admins');
select (select payload ->> 'request_id' from c3_request) as c3_request_id \gset

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e2000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e2000000-0000-4000-8000-000000000002')::text, true);
create temp table c3_approve as
select public.approve_workspace_deletion(:'c3_request_id'::uuid) as payload;
select is((select payload ->> 'status' from c3_approve), 'approved',
        'unanimous approval marks the request approved');
select is((select (payload ->> 'approved_count')::integer from c3_approve), 2,
        'both admins are recorded as approvers');

create temp table c3_withdraw as
select public.withdraw_workspace_deletion_approval(:'c3_request_id'::uuid) as payload;
select is((select payload ->> 'status' from c3_withdraw), 'pending',
        'withdrawal returns an approved request to pending');

create temp table c3_reapprove as
select public.approve_workspace_deletion(:'c3_request_id'::uuid) as payload;
select is((select payload ->> 'status' from c3_reapprove), 'approved',
        're-approval restores unanimity');

set local role postgres;
update private.workspace_deletion_requests
set execute_after = clock_timestamp() - interval '1 second'
where request_id = :'c3_request_id'::uuid;

select is(
    (private.execute_workspace_deletion(:'c3_request_id'::uuid) ->> 'status'),
    'executed',
    'the privileged worker executes the approved request'
);
select is(
    (select count(*)::integer from public.workspaces where id = :'c3_workspace_id'::uuid),
    0,
    'the approved workspace is removed'
);
select ok(
    not pg_catalog.has_function_privilege(
        'authenticated', 'public.delete_group_workspace(uuid)', 'EXECUTE'
    ),
    'direct group workspace deletion is closed'
);

select * from finish();
rollback;
