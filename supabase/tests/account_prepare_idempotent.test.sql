begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(6);

insert into auth.users (id, aud, role, email) values
    ('f6000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'reprepare-owner@example.test');

insert into public.workspaces (id, name, personal_owner_id) values (
    'f6100000-0000-4000-8000-000000000001',
    'Reprepare personal workspace',
    'f6000000-0000-4000-8000-000000000001'
);
insert into public.workspace_memberships (workspace_id, user_id, role, status) values (
    'f6100000-0000-4000-8000-000000000001',
    'f6000000-0000-4000-8000-000000000001',
    'owner', 'active'
);
insert into public.evidence_records (id, workspace_id, content) values (
    'f6200000-0000-4000-8000-000000000001',
    'f6100000-0000-4000-8000-000000000001',
    'evidence removed by re-prepare'
);
set local role authenticated;
select set_config('request.jwt.claim.sub', 'f6000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'f6000000-0000-4000-8000-000000000001')::text, true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    'f6300000-0000-4000-8000-000000000001',
    'f6000000-0000-4000-8000-000000000001', 'account',
    'f6000000-0000-4000-8000-000000000001', 'reprepare-key'
);

set local role postgres;
update public.data_deletion_requests
set status = 'purge_pending', accepted_at = clock_timestamp()
where request_id = 'f6300000-0000-4000-8000-000000000001';

-- Simulate the historical v2 marker: account_prepared already exists while
-- the workspace has not been removed.
insert into public.evidence_deletion_audit (
    actor_user_id, workspace_id, resource_type, resource_id, action, result,
    request_id, scope_type, metadata
) values (
    'f6000000-0000-4000-8000-000000000001', null, 'deletion_request',
    'f6300000-0000-4000-8000-000000000001', 'account_prepared', 'success',
    'f6300000-0000-4000-8000-000000000001', 'account', '{}'::jsonb
);

set local role account_deletion_worker;
select is((private.prepare_individual_account_purge_v3(
    'f6300000-0000-4000-8000-000000000001'
) ->> 'workspaces_deleted')::integer, 1,
    're-prepare removes the workspace after a historical prepared marker');

set local role postgres;
select is((select count(*)::integer from public.workspaces
    where id = 'f6100000-0000-4000-8000-000000000001'),
    0, 'the personal workspace is deleted');
select is((select count(*)::integer from public.evidence_records
    where workspace_id = 'f6100000-0000-4000-8000-000000000001'),
    0, 'the workspace evidence is deleted');
select is((select count(*)::integer from public.workspace_memberships
    where workspace_id = 'f6100000-0000-4000-8000-000000000001'),
    0, 'the workspace membership is deleted');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = 'f6300000-0000-4000-8000-000000000001'
      and action = 'account_prepared'),
    1, 'the append-only prepared audit is not duplicated');

select is((select status from public.data_deletion_requests
    where request_id = 'f6300000-0000-4000-8000-000000000001'),
    'purge_pending', 're-prepare keeps the request runnable');

select * from finish();
rollback;
