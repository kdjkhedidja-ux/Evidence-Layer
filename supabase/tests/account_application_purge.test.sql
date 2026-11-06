begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(11);

insert into auth.users (id, aud, role, email)
values
    ('91000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'p4-member@example.test'),
    ('91000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'p4-owner@example.test'),
    ('91000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'p4-other@example.test');

insert into public.workspaces (id, name)
values ('92000000-0000-4000-8000-000000000001', 'P4 shared workspace');
insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('92000000-0000-4000-8000-000000000001', '91000000-0000-4000-8000-000000000001', 'member', 'active'),
    ('92000000-0000-4000-8000-000000000001', '91000000-0000-4000-8000-000000000002', 'owner', 'active'),
    ('92000000-0000-4000-8000-000000000001', '91000000-0000-4000-8000-000000000003', 'member', 'active');
insert into public.source_grants (
    workspace_id, source_id, principal_id, grant_type, granted_by
) values (
    '92000000-0000-4000-8000-000000000001',
    '93000000-0000-4000-8000-000000000001',
    '91000000-0000-4000-8000-000000000001', 'read',
    '91000000-0000-4000-8000-000000000002'
);
insert into public.evidence_records (id, workspace_id, content)
values (
    '94000000-0000-4000-8000-000000000001',
    '92000000-0000-4000-8000-000000000001', 'shared evidence remains'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '91000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"91000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    '95000000-0000-4000-8000-000000000001',
    '91000000-0000-4000-8000-000000000001', 'account',
    '91000000-0000-4000-8000-000000000001', 'p4-key'
);

grant account_lifecycle_transition_writer to postgres with set true, inherit false;
set local role account_lifecycle_transition_writer;
update public.data_deletion_requests
set status = 'accepted', accepted_at = clock_timestamp()
where request_id = '95000000-0000-4000-8000-000000000001';

set local role account_deletion_worker;
select ok((private.run_account_deletion_slice(
    '95000000-0000-4000-8000-000000000001'
) ->> 'status') = 'purge_pending', 'P4 application slice completes verification');
select is((select count(*)::integer from public.data_deletion_work_set
    where request_id = '95000000-0000-4000-8000-000000000001'),
    2, 'work set freezes one grant and one principal membership');
select is((select count(*)::integer from public.source_grants
    where principal_id = '91000000-0000-4000-8000-000000000001'),
    0, 'principal source grant is removed');
select is((select count(*)::integer from public.workspace_memberships
    where user_id = '91000000-0000-4000-8000-000000000001'),
    0, 'principal member memberships are removed');
select is((select count(*)::integer from public.workspace_memberships
    where workspace_id = '92000000-0000-4000-8000-000000000001'),
    2, 'owner and other member memberships remain');
select is((select count(*)::integer from public.evidence_records
    where id = '94000000-0000-4000-8000-000000000001'),
    1, 'shared workspace evidence remains');
select is((select status from public.data_deletion_requests
    where request_id = '95000000-0000-4000-8000-000000000001'),
    'purge_pending', 'partial application purge is not terminal');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = '95000000-0000-4000-8000-000000000001'
      and action = 'workset_frozen'),
    1, 'work set freeze is audited');

set local role postgres;
select ok((select state = 'purge_pending'
    from public.account_lifecycle
    where subject_id = '91000000-0000-4000-8000-000000000001'),
    'account remains blocked after the partial application slice');
select is((select count(*)::integer from public.data_deletion_work_set
    where request_id = '95000000-0000-4000-8000-000000000001'
      and target_type = 'workspace_membership'),
    1, 'work set contains only the principal membership');
select ok(not exists (
    select 1 from pg_catalog.pg_roles
    where rolname = 'account_deletion_worker' and rolcanlogin
), 'worker credentials cannot be used as a login role');

revoke account_lifecycle_transition_writer from postgres;
select * from finish();
rollback;
