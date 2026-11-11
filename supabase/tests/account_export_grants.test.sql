begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(13);

insert into auth.users (id, aud, role, email) values
    ('e1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'export-owner@example.test'),
    ('e1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'export-other@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);

select is((public.finalize_account_export(
    'e2000000-0000-4000-8000-000000000001',
    'export-key-1',
    'aGVsbG8=',
    '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
    5,
    '{}'::jsonb,
    repeat('b', 64),
    repeat('c', 64)
) ->> 'status'), 'ready', 'a ready export is finalized for the requester');

set local role postgres;
select is((select count(*)::integer from public.data_export_grants
    where export_id = 'e2000000-0000-4000-8000-000000000001'), 1,
    'one grant is issued');
select is((select artifact_base64 from public.data_export_jobs
    where export_id = 'e2000000-0000-4000-8000-000000000001'), 'aGVsbG8=',
    'the private artifact is stored');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001')::text, true);
select is((public.redeem_account_export_grant(
    'e2000000-0000-4000-8000-000000000001', repeat('c', 64)
) ->> 'artifact_base64'), 'aGVsbG8=', 'the grant releases the artifact once');
select ok(public.redeem_account_export_grant(
    'e2000000-0000-4000-8000-000000000001', repeat('c', 64)
) is null, 'the same grant cannot be redeemed twice');

select is((public.finalize_account_export(
    'e2000000-0000-4000-8000-000000000002',
    'export-key-2',
    'aGVsbG8=',
    '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
    5,
    '{}'::jsonb,
    repeat('b', 64),
    repeat('d', 64)
) ->> 'status'), 'ready', 'a second export is finalized');
select is((public.revoke_account_export(
    'e2000000-0000-4000-8000-000000000002'
) ->> 'status'), 'revoked', 'the requester can revoke an export');
select ok(public.redeem_account_export_grant(
    'e2000000-0000-4000-8000-000000000002', repeat('d', 64)
) is null, 'a revoked grant cannot be redeemed');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000002')::text, true);
select ok(public.redeem_account_export_grant(
    'e2000000-0000-4000-8000-000000000001', repeat('c', 64)
) is null, 'another user cannot redeem the grant');

set local role postgres;
select is((public.finalize_account_export(
    'e2000000-0000-4000-8000-000000000003',
    'export-key-3',
    'aGVsbG8=',
    '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
    5,
    '{}'::jsonb,
    repeat('b', 64),
    repeat('e', 64)
) ->> 'status'), 'ready', 'a cleanup fixture is finalized');
update public.data_export_jobs
   set ready_at = pg_catalog.clock_timestamp() - interval '2 hours',
       expires_at = pg_catalog.clock_timestamp() - interval '1 hour'
 where export_id = 'e2000000-0000-4000-8000-000000000003';
select is(private.cleanup_expired_exports(), 1, 'expired exports are cleaned');
select is((select status from public.data_export_jobs
    where export_id = 'e2000000-0000-4000-8000-000000000003'), 'expired',
    'the expired export is marked expired');
select ok((select artifact_base64 is null from public.data_export_jobs
    where export_id = 'e2000000-0000-4000-8000-000000000003'),
    'the expired artifact is removed');

select * from finish();
rollback;
