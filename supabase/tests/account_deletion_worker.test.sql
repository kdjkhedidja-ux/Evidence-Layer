begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(11);

select ok((select not rolcanlogin and not rolinherit and not rolbypassrls
    from pg_catalog.pg_roles
    where rolname = 'account_deletion_worker'),
    'worker role is non-login, non-inheriting, and not BYPASSRLS');
select ok(not pg_catalog.has_table_privilege(
    'account_deletion_worker', 'public.source_snapshots', 'SELECT'
), 'worker cannot read snapshot catalog rows');
select ok(pg_catalog.has_table_privilege(
    'account_deletion_worker', 'public.source_grants', 'DELETE'
), 'worker has only the implemented source-grant delete capability');

insert into auth.users (id, aud, role, email)
values ('81000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'worker-owner@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', '81000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"81000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    '82000000-0000-4000-8000-000000000001',
    '81000000-0000-4000-8000-000000000001', 'account',
    '81000000-0000-4000-8000-000000000001', 'worker-test-key'
);

set local role account_lifecycle_transition_writer;
update public.data_deletion_requests
set status = 'accepted', accepted_at = clock_timestamp()
where request_id = '82000000-0000-4000-8000-000000000001';

update public.account_lifecycle
set state = 'revocation_pending', request_id = '82000000-0000-4000-8000-000000000001'
where subject_id = (select requester_user_id from public.data_deletion_requests where request_id = '82000000-0000-4000-8000-000000000001');

set local role account_deletion_worker;
select ok((private.run_account_deletion_slice(
    '82000000-0000-4000-8000-000000000001'
) ->> 'status') = 'purge_pending',
    'worker claim/effect/verification leaves the partial slice purge_pending');
select is((select status from public.data_deletion_requests
    where request_id = '82000000-0000-4000-8000-000000000001'),
    'purge_pending', 'request records the claimed intermediate state');
select is((select state from public.account_lifecycle
    where subject_id = '81000000-0000-4000-8000-000000000001'),
    'purge_pending', 'worker transitions the account denial lifecycle');
select is((select count(*)::integer from public.source_grants
    where principal_id = '81000000-0000-4000-8000-000000000001'),
    0, 'implemented source-grant slice verifies no grants remain');
select throws_ok(
    $q$select private.run_account_deletion_slice('82000000-0000-4000-8000-000000000001')$q$,
    '55000', 'deletion request is not claimable',
    'a second claim cannot execute the same request');

set local role postgres;
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = '82000000-0000-4000-8000-000000000001'
      and action in ('purge_claimed', 'purge_effect', 'purge_verified')),
    3, 'claim, effect, and verification are audited');
select ok((select state = 'purge_pending' and request_id =
        '82000000-0000-4000-8000-000000000001'
    from public.account_lifecycle
    where subject_id = '81000000-0000-4000-8000-000000000001'),
    'successful partial slice does not claim terminal purge');
select ok(not exists (
    select 1 from pg_catalog.pg_auth_members
    where member = 'account_deletion_worker'::pg_catalog.regrole
), 'worker does not inherit another database role');

revoke account_lifecycle_transition_writer from postgres;
select * from finish();
rollback;
