begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(13);

insert into auth.users (id, aud, role, email) values
    ('f1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'org-purge-owner@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'f1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'f1000000-0000-4000-8000-000000000001')::text, true);

select public.create_organization_account('Purge Org');
select (public.create_group_workspace('Purge group workspace') ->> 'workspace_id')::uuid
    as group_workspace_id \gset

select is((select role from public.workspace_memberships
    where workspace_id = :'group_workspace_id'::uuid
      and user_id = 'f1000000-0000-4000-8000-000000000001'),
    'admin', 'the organization owner is an admin, not a workspace owner');

set local role postgres;
insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes,
    state, created_by
) values (
    :'group_workspace_id'::uuid,
    'f2000000-0000-4000-8000-000000000001',
    'f3000000-0000-4000-8000-000000000001',
    'workspace/' || :'group_workspace_id' || '/source/f2000000-0000-4000-8000-000000000001/version/f3000000-0000-4000-8000-000000000001',
    repeat('a', 64), 128, 'staged',
    'f1000000-0000-4000-8000-000000000001'
);
insert into public.evidence_records (id, workspace_id, content) values (
    'f4000000-0000-4000-8000-000000000001',
    :'group_workspace_id'::uuid, 'group evidence must be purged'
);
select is((select count(*)::integer from public.source_snapshots
    where workspace_id = :'group_workspace_id'::uuid),
    1, 'the group workspace has a snapshot that would restrict deletion');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'f1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'f1000000-0000-4000-8000-000000000001')::text, true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    'f5000000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001', 'account',
    'f1000000-0000-4000-8000-000000000001', 'org-purge-key'
);

set local role account_lifecycle_transition_writer;
update public.data_deletion_requests
set status = 'accepted', accepted_at = clock_timestamp()
where request_id = 'f5000000-0000-4000-8000-000000000001';
update public.account_lifecycle
set state = 'revocation_pending', request_id = 'f5000000-0000-4000-8000-000000000001'
where subject_id = 'f1000000-0000-4000-8000-000000000001';

set local role account_deletion_worker;
select ok((private.run_account_deletion_slice(
    'f5000000-0000-4000-8000-000000000001'
) ->> 'status') = 'purge_pending', 'the scoped slice completes');
select ok((private.prepare_individual_account_purge_v3(
    'f5000000-0000-4000-8000-000000000001'
) ->> 'status') = 'prepared', 'account preparation completes for an organization owner');

set local role postgres;
select is((select count(*)::integer from public.source_snapshots
    where workspace_id = :'group_workspace_id'::uuid),
    0, 'the group workspace snapshot is deleted before workspace cascade');
select is((select count(*)::integer from public.workspaces
    where id = :'group_workspace_id'::uuid),
    0, 'the organization-owned group workspace is deleted');
select is((select count(*)::integer from public.evidence_records
    where workspace_id = :'group_workspace_id'::uuid),
    0, 'group workspace evidence is removed');
select is((select count(*)::integer from public.data_deletion_storage_queue
    where object_key like 'workspace/' || :'group_workspace_id' || '/%'),
    1, 'the snapshot object key is queued for Storage cleanup');

set local role account_deletion_worker;
select is((private.delete_account_lifecycle_for_purge(
    'f5000000-0000-4000-8000-000000000001'
) ->> 'deleted')::boolean, true, 'the lifecycle row is removed before Auth deletion');

set local role postgres;
delete from auth.users where id = 'f1000000-0000-4000-8000-000000000001';
select is((select count(*)::integer from auth.users
    where id = 'f1000000-0000-4000-8000-000000000001'),
    0, 'the Auth identity can be deleted without a snapshot FK block');
select is((select count(*)::integer from public.organizations
    where owner_user_id = 'f1000000-0000-4000-8000-000000000001'),
    0, 'the organization cascade completes');

set local role account_deletion_worker;
select is((private.mark_account_purged_v3(
    'f5000000-0000-4000-8000-000000000001'
) ->> 'status'), 'purged', 'the request reaches the terminal purged state');

set local role postgres;
select is((select status from public.data_deletion_requests
    where request_id = 'f5000000-0000-4000-8000-000000000001'),
    'purged', 'the terminal request status is persisted');

select * from finish();
rollback;
