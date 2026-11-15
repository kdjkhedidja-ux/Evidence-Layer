begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(15);

insert into auth.users (id, aud, role, email)
values
    ('83000000-0000-4000-8000-000000000011', 'authenticated', 'authenticated', 'queue-owner@example.test'),
    ('83000000-0000-4000-8000-000000000012', 'authenticated', 'authenticated', 'queue-worker@example.test');

insert into public.workspaces (id, name)
values ('a3000000-0000-4000-8000-000000000011', 'Queue workspace');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a3000000-0000-4000-8000-000000000011', '83000000-0000-4000-8000-000000000011', 'owner', 'active');

insert into private.snapshot_verification_worker (user_id)
values ('83000000-0000-4000-8000-000000000012');

-- Stage a snapshot as its owner; the insert trigger must enqueue automatically.
set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000011', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000011","role":"authenticated","aud":"authenticated"}', true);
insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes, state
) values (
    'a3000000-0000-4000-8000-000000000011',
    'c3000000-0000-4000-8000-000000000011',
    'd3000000-0000-4000-8000-000000000011',
    'workspace/a3000000-0000-4000-8000-000000000011/source/c3000000-0000-4000-8000-000000000011/version/d3000000-0000-4000-8000-000000000011',
    repeat('a', 64), 5, 'staged'
);

set local role postgres;
select is(
    (select count(*)::integer from private.snapshot_verification_jobs
      where workspace_id = 'a3000000-0000-4000-8000-000000000011'
        and state = 'queued'),
    1,
    'staging enqueues exactly one verification job'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000012', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000012","role":"authenticated","aud":"authenticated"}', true);
select out_job_id as claim_job_id, out_attempts as claim_attempts
  from public.claim_snapshot_verification('worker-a', 300)
\gset
select is(:'claim_attempts'::integer, 1, 'first claim records one attempt');

set local role postgres;
select is(
    (select state from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    'claimed',
    'claim moves the job to claimed'
);
select is(
    (select lease_user_id from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    '83000000-0000-4000-8000-000000000012'::uuid,
    'claim records the worker identity'
);
select is(
    (select count(*)::integer from private.snapshot_verifier_allowlist
      where user_id = '83000000-0000-4000-8000-000000000012'
        and workspace_id = 'a3000000-0000-4000-8000-000000000011'),
    1,
    'claim auto-authorizes the worker for the job workspace'
);
select is(
    (select count(*)::integer from public.claim_snapshot_verification('worker-b', 300)),
    0,
    'a claimed lease cannot be claimed again'
);

set local role authenticated;
select public.fail_snapshot_verification(
    :'claim_job_id'::uuid, 'worker-a', 'transient_error'
);

set local role postgres;
select is(
    (select state from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    'retryable',
    'transient failure becomes retryable'
);
select is(
    (select error_category from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    'transient_error',
    'transient failure records its category'
);
select ok(
    (select next_attempt_at is not null from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    'retryable failure schedules a backoff'
);

set local role authenticated;
select is(
    (select count(*)::integer from public.claim_snapshot_verification('worker-b', 300)),
    0,
    'a job waiting for backoff is not claimable'
);

set local role postgres;
update private.snapshot_verification_jobs
set next_attempt_at = clock_timestamp() - interval '1 second'
where job_id = :'claim_job_id'::uuid;

set local role authenticated;
select out_attempts as retry_attempts
  from public.claim_snapshot_verification('worker-b', 300)
\gset
select is(:'retry_attempts'::integer, 2, 'claiming a due retry increments attempts');

select public.fail_snapshot_verification(
    :'claim_job_id'::uuid, 'worker-b', 'verification_failed'
);

set local role postgres;
select is(
    (select state from private.snapshot_verification_jobs where job_id = :'claim_job_id'::uuid),
    'dead',
    'a verification mismatch is terminal'
);

set local role authenticated;
select is(
    (select count(*)::integer from public.claim_snapshot_verification('worker-c', 300)),
    0,
    'a terminal job is never claimed again'
);

select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000011', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000011","role":"authenticated","aud":"authenticated"}', true);
select throws_ok(
    $$select * from public.claim_snapshot_verification('owner-as-worker', 300)$$,
    '42501',
    null,
    'a non-worker cannot claim verification jobs'
);

select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000012', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000012","role":"authenticated","aud":"authenticated"}', true);
set local role postgres;
update private.snapshot_verification_jobs
set state = 'claimed',
    lease_owner = 'worker-d',
    lease_expires_at = clock_timestamp() + interval '5 minutes',
    next_attempt_at = null
where job_id = :'claim_job_id'::uuid;

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000012', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000012","role":"authenticated","aud":"authenticated"}', true);
select throws_ok(
    format('select public.complete_snapshot_verification(%L, %L)', :'claim_job_id', 'worker-d'),
    '55000',
    null,
    'completion fails until the snapshot is published'
);

select * from finish();
rollback;
