begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(7);

insert into auth.users (id, aud, role, email)
values ('83000000-0000-4000-8000-000000000041', 'authenticated', 'authenticated', 'cleanup-owner@example.test');

insert into public.workspaces (id, name)
values ('a6000000-0000-4000-8000-000000000041', 'Cleanup workspace');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a6000000-0000-4000-8000-000000000041', '83000000-0000-4000-8000-000000000041', 'owner', 'active');

insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by, created_at
) values
    ('a6000000-0000-4000-8000-000000000041', 'c6000000-0000-4000-8000-000000000041', 'd6000000-0000-4000-8000-000000000041',
     'workspace/a6000000-0000-4000-8000-000000000041/source/c6000000-0000-4000-8000-000000000041/version/d6000000-0000-4000-8000-000000000041',
     repeat('a', 64), 10, 'staged', '83000000-0000-4000-8000-000000000041', clock_timestamp() - interval '30 days'),
    ('a6000000-0000-4000-8000-000000000041', 'c6000000-0000-4000-8000-000000000041', 'd6000000-0000-4000-8000-000000000042',
     'workspace/a6000000-0000-4000-8000-000000000041/source/c6000000-0000-4000-8000-000000000041/version/d6000000-0000-4000-8000-000000000042',
     repeat('b', 64), 20, 'staged', '83000000-0000-4000-8000-000000000041', clock_timestamp() - interval '1 day');

insert into private.snapshot_verification_jobs (workspace_id, source_id, version_id)
values ('a6000000-0000-4000-8000-000000000041', 'c6000000-0000-4000-8000-000000000041', 'd6000000-0000-4000-8000-000000000042');

select is(
    private.enqueue_stale_staged_sources(14, 10),
    1,
    'only the stale staged source without an active job is enqueued'
);
select ok(
    (select abandoned_at is not null from public.source_snapshots
      where version_id = 'd6000000-0000-4000-8000-000000000041'),
    'the stale source is marked abandoned'
);
select is(
    (select count(*)::integer from private.source_object_cleanup_queue),
    1,
    'one cleanup object is queued'
);

select out_queue_id as cleanup_queue_id, out_attempts as cleanup_attempts
  from private.claim_source_cleanup('cleanup-worker', 300)
\gset
select is(:'cleanup_attempts'::integer, 1, 'cleanup claim records one attempt');

select private.complete_source_cleanup(:'cleanup_queue_id'::uuid, 'cleanup-worker');

select is(
    (select state from private.source_object_cleanup_queue where queue_id = :'cleanup_queue_id'::uuid),
    'deleted',
    'cleanup completion marks the queue deleted'
);
select ok(
    (select object_deleted_at is not null from public.source_snapshots
      where version_id = 'd6000000-0000-4000-8000-000000000041'),
    'cleanup completion records object deletion'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000041', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000041","role":"authenticated","aud":"authenticated"}', true);
select is(
    (public.workspace_source_usage('a6000000-0000-4000-8000-000000000041') ->> 'version_count')::integer,
    1,
    'quota usage excludes the abandoned source'
);

select * from finish();
rollback;
