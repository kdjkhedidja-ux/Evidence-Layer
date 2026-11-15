begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(28);

select ok((select not rolcanlogin and not rolinherit and not rolbypassrls
    from pg_catalog.pg_roles
    where rolname = 'account_deletion_worker'),
    'worker role is non-login, non-inheriting, and not BYPASSRLS');

insert into auth.users (id, aud, role, email)
values ('83000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'lease-owner@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    '84000000-0000-4000-8000-000000000001',
    '83000000-0000-4000-8000-000000000001', 'account',
    '83000000-0000-4000-8000-000000000001', 'lease-retry-test-key'
);

set local role account_lifecycle_transition_writer;
update public.data_deletion_requests
set status = 'accepted', accepted_at = clock_timestamp()
where request_id = '84000000-0000-4000-8000-000000000001';

set local role account_deletion_worker;
select is(
    private.claim_deletion_request('worker-a', 300, 5) ->> 'previous_status',
    'accepted',
    'first claim records the accepted previous state'
);
set local role postgres;
select is(
    (select attempts from public.data_deletion_requests
      where request_id = '84000000-0000-4000-8000-000000000001'),
    1,
    'first claim records one attempt'
);

set local role account_deletion_worker;
select is(
    private.claim_deletion_request('worker-a', 300, 5) is null,
    true,
    'a request already under an active lease cannot be claimed again'
);

set local role postgres;
select is((select status from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'accepted',
    'claiming an accepted request leaves the scoped-slice status intact');
select is((select lease_owner from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'worker-a', 'claim records the lease owner');
select ok((select lease_expires_at is not null from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'claim records a lease expiry');

set local role account_deletion_worker;
select is(
    (private.record_deletion_failure(
        '84000000-0000-4000-8000-000000000001', 'worker-a', 'worker_error', 5
    ) ->> 'terminal')::boolean,
    false,
    'a retryable worker error is not terminal'
);

set local role postgres;
select is((select status from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'purge_failed', 'failure records purge_failed');
select is((select purge_error_category from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'worker_error', 'failure records the category');
select ok((select lease_owner is null and lease_expires_at is null
    from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'failure releases the lease');
select ok((select next_attempt_at is not null from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'retryable failure schedules a backoff');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = '84000000-0000-4000-8000-000000000001'
      and action = 'purge_failed' and result = 'failure'),
    1, 'worker failure is audited once');

set local role account_deletion_worker;
select ok(
    private.claim_deletion_request('worker-b', 300, 5) is null,
    'a request waiting for its backoff is not claimable'
);

set local role postgres;
update public.data_deletion_requests
set next_attempt_at = clock_timestamp() - interval '1 second'
where request_id = '84000000-0000-4000-8000-000000000001';

set local role account_deletion_worker;
select is(
    private.claim_deletion_request('worker-b', 300, 5) ->> 'previous_status',
    'purge_failed',
    'a due retry records the failed previous state'
);
set local role postgres;
select is(
    (select attempts from public.data_deletion_requests
      where request_id = '84000000-0000-4000-8000-000000000001'),
    2,
    'claiming a due retry increments the attempt count'
);
select is((select status from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'purge_pending', 'a claimed retry returns to purge_pending');
select ok((select purge_error_category is null
    from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'a claimed retry clears the previous category');

set local role account_deletion_worker;
select is(
    (private.record_deletion_failure(
        '84000000-0000-4000-8000-000000000001', 'worker-b',
        'verification_failed', 5
    ) ->> 'terminal')::boolean,
    true,
    'verification failures are terminal'
);

set local role postgres;
select ok((select next_attempt_at is null from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'terminal failure has no retry schedule');
select is((select purge_error_category from public.data_deletion_requests
    where request_id = '84000000-0000-4000-8000-000000000001'),
    'verification_failed', 'terminal failure keeps its category');

set local role account_deletion_worker;
select ok(
    private.claim_deletion_request('worker-c', 300, 5) is null,
    'a terminal verification failure is never claimed again'
);

set local role postgres;
select ok(not pg_catalog.has_function_privilege(
    'anon', 'private.claim_deletion_request(text, integer, integer)', 'EXECUTE'
), 'anon cannot claim deletion work');
select ok(not pg_catalog.has_function_privilege(
    'authenticated', 'private.claim_deletion_request(text, integer, integer)', 'EXECUTE'
), 'authenticated cannot claim deletion work');
select ok(not pg_catalog.has_function_privilege(
    'service_role', 'private.claim_deletion_request(text, integer, integer)', 'EXECUTE'
), 'service_role cannot claim deletion work');
select ok(pg_catalog.has_function_privilege(
    'account_deletion_worker', 'private.claim_deletion_request(text, integer, integer)', 'EXECUTE'
), 'the dedicated worker can claim deletion work');
select ok((select prosecdef from pg_catalog.pg_proc
    where oid = 'private.claim_deletion_request(text, integer, integer)'::regprocedure),
    'claim function is security definer');
select ok((select proconfig from pg_catalog.pg_proc
    where oid = 'private.claim_deletion_request(text, integer, integer)'::regprocedure)
    @> array['search_path=""'],
    'claim function pins an empty search_path');

select * from finish();
rollback;
