begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(5);

insert into auth.users (id, aud, role, email)
values
    ('83000000-0000-4000-8000-000000000031', 'authenticated', 'authenticated', 'quota-owner@example.test'),
    ('83000000-0000-4000-8000-000000000032', 'authenticated', 'authenticated', 'quota-other@example.test');

insert into public.workspaces (id, name)
values ('a5000000-0000-4000-8000-000000000031', 'Quota workspace');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a5000000-0000-4000-8000-000000000031', '83000000-0000-4000-8000-000000000031', 'owner', 'active');

insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by
) values
    ('a5000000-0000-4000-8000-000000000031', 'c5000000-0000-4000-8000-000000000031', 'd5000000-0000-4000-8000-000000000031',
     'workspace/a5000000-0000-4000-8000-000000000031/source/c5000000-0000-4000-8000-000000000031/version/d5000000-0000-4000-8000-000000000031',
     repeat('a', 64), 10, 'staged', '83000000-0000-4000-8000-000000000031'),
    ('a5000000-0000-4000-8000-000000000031', 'c5000000-0000-4000-8000-000000000031', 'd5000000-0000-4000-8000-000000000032',
     'workspace/a5000000-0000-4000-8000-000000000031/source/c5000000-0000-4000-8000-000000000031/version/d5000000-0000-4000-8000-000000000032',
     repeat('b', 64), 20, 'staged', '83000000-0000-4000-8000-000000000031');

insert into private.workspace_invitations (
    workspace_id, email, role, token_hash, created_by, expires_at
) values (
    'a5000000-0000-4000-8000-000000000031', 'quota-invite@example.test', 'member',
    repeat('c', 64), '83000000-0000-4000-8000-000000000031', clock_timestamp() + interval '7 days'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000031', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000031","role":"authenticated","aud":"authenticated"}', true);
select is(
    (public.workspace_source_usage('a5000000-0000-4000-8000-000000000031') ->> 'version_count')::integer,
    2, 'usage counts source versions');
select is(
    (public.workspace_source_usage('a5000000-0000-4000-8000-000000000031') ->> 'total_bytes')::bigint,
    30::bigint, 'usage sums source bytes');
select is(
    (public.workspace_source_usage('a5000000-0000-4000-8000-000000000031') ->> 'staged_count')::integer,
    2, 'usage counts staged versions');
select is(
    (public.workspace_source_usage('a5000000-0000-4000-8000-000000000031') ->> 'pending_invitations')::integer,
    1, 'usage counts pending invitations');

select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000032', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000032","role":"authenticated","aud":"authenticated"}', true);
select throws_ok(
    $$select * from public.workspace_source_usage('a5000000-0000-4000-8000-000000000031')$$,
    '42501', null, 'a non-owner cannot read workspace usage'
);

select * from finish();
rollback;
