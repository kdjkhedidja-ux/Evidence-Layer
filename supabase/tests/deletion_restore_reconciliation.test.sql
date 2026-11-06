begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(9);

insert into auth.users (id, aud, role, email)
values ('b1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'p5-owner@example.test');
insert into public.workspaces (id, name)
values ('b2000000-0000-4000-8000-000000000001', 'P5 restore workspace');
insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('b2000000-0000-4000-8000-000000000001', 'b1000000-0000-4000-8000-000000000001', 'owner', 'active');
insert into public.evidence_records (id, workspace_id, content)
values ('b3000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001', 'restore test evidence');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'b1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"b1000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    'b4000000-0000-4000-8000-000000000001',
    'b1000000-0000-4000-8000-000000000001', 'account',
    'b1000000-0000-4000-8000-000000000001', 'p5-key'
);
grant account_lifecycle_transition_writer to postgres with set true, inherit false;
set local role account_lifecycle_transition_writer;
update public.data_deletion_requests set status = 'accepted', accepted_at = clock_timestamp()
where request_id = 'b4000000-0000-4000-8000-000000000001';

set local role account_deletion_worker;
select ok((private.run_account_deletion_slice(
    'b4000000-0000-4000-8000-000000000001'
) ->> 'status') = 'purge_pending', 'worker slice reaches purge_pending');
select is((select count(*)::integer from public.evidence_records
    where id = 'b3000000-0000-4000-8000-000000000001'), 0,
    'successful slice removes the frozen evidence target');
select is((select state from public.account_lifecycle
    where subject_id = 'b1000000-0000-4000-8000-000000000001'), 'purge_pending',
    'data plane remains denied after slice');

select ok((private.reconcile_account_deletion_slice(
    'b4000000-0000-4000-8000-000000000001'
) ->> 'reconciled')::boolean, 'clean local state reconciles without opening account');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = 'b4000000-0000-4000-8000-000000000001'
      and action = 'reconciliation_verified'), 1,
    'clean reconciliation is audited');

-- Simulate a stale backup reintroducing the already-purged row while the
-- lifecycle marker remains blocked. This is local-only test data.
set local role postgres;
insert into public.evidence_records (id, workspace_id, content)
values ('b3000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001', 'restored stale row');
set local role account_deletion_worker;
select is((private.reconcile_account_deletion_slice(
    'b4000000-0000-4000-8000-000000000001'
) ->> 'status'), 'purge_failed', 'restored target fails reconciliation');
select is((select status from public.data_deletion_requests
    where request_id = 'b4000000-0000-4000-8000-000000000001'), 'purge_failed',
    'request records restored-target failure');
select is((select state from public.account_lifecycle
    where subject_id = 'b1000000-0000-4000-8000-000000000001'), 'purge_failed',
    'lifecycle remains blocked after restore inconsistency');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = 'b4000000-0000-4000-8000-000000000001'
      and action = 'reconciliation_failed'), 1,
    'restore inconsistency is audited without content');

revoke account_lifecycle_transition_writer from postgres;
select * from finish();
rollback;
