begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(16);

select ok((select relrowsecurity and relforcerowsecurity
    from pg_catalog.pg_class
    where oid = 'public.data_deletion_requests'::pg_catalog.regclass),
    'deletion requests force RLS');
select ok((select relrowsecurity and relforcerowsecurity
    from pg_catalog.pg_class
    where oid = 'public.data_deletion_manifest'::pg_catalog.regclass),
    'deletion manifest forces RLS');
select is((select pg_catalog.pg_get_constraintdef(oid)
    from pg_catalog.pg_constraint
    where conrelid = 'public.data_deletion_requests'::pg_catalog.regclass
      and conname = 'data_deletion_requests_account_scope_check'),
    'CHECK ((scope_id = requester_user_id))',
    'requests are account-scoped to the requesting principal');
select ok(exists (
    select 1 from pg_catalog.pg_indexes
    where schemaname = 'public'
      and tablename = 'data_deletion_requests'
      and indexname = 'data_deletion_requests_open_scope_key'
), 'only one open account request exists per principal');
select ok(not pg_catalog.has_table_privilege('authenticated', 'public.data_deletion_manifest', 'UPDATE')
    and not pg_catalog.has_table_privilege('authenticated', 'public.data_deletion_manifest', 'DELETE'),
    'authenticated clients cannot mutate manifests');
select ok(not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'UPDATE')
    and not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'DELETE'),
    'authenticated clients cannot mutate deletion audit events');

insert into auth.users (id, aud, role, email)
values
    ('71000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'p1-owner@example.test'),
    ('71000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'p1-other@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', '71000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"71000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);

insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    '72000000-0000-4000-8000-000000000001',
    '71000000-0000-4000-8000-000000000001', 'account',
    '71000000-0000-4000-8000-000000000001', 'p1-idempotency-key'
);

select is((select status from public.data_deletion_requests
    where request_id = '72000000-0000-4000-8000-000000000001'),
    'approval_pending', 'request starts in approval_pending');
select is((select requester_user_id::text from public.data_deletion_requests
    where request_id = '72000000-0000-4000-8000-000000000001'),
    '71000000-0000-4000-8000-000000000001', 'request records the verified principal');

select status from public.accept_account_deletion(
    '72000000-0000-4000-8000-000000000001'
);

select is((select state from public.account_lifecycle
    where subject_id = '71000000-0000-4000-8000-000000000001'),
    'revocation_pending', 'acceptance moves the account into the existing denial state');
select is((select count(*)::integer from public.data_deletion_manifest
    where request_id = '72000000-0000-4000-8000-000000000001'),
    1, 'acceptance freezes one account target');
select is((select count(*)::integer from public.data_deletion_manifest
    where request_id = '72000000-0000-4000-8000-000000000001'
      and target_type = 'account'
      and target_id = '71000000-0000-4000-8000-000000000001'),
    1, 'manifest contains only the type/id target reference');

select status from public.accept_account_deletion(
    '72000000-0000-4000-8000-000000000001'
);

select is((select count(*)::integer from public.data_deletion_manifest
    where request_id = '72000000-0000-4000-8000-000000000001'),
    1, 'acceptance replay does not create a divergent manifest');

select set_config('request.jwt.claim.sub', '71000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"71000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select is((select count(*)::integer from public.data_deletion_requests
    where request_id = '72000000-0000-4000-8000-000000000001'),
    0, 'another principal cannot read the request');
select is((select count(*)::integer from public.data_deletion_manifest
    where request_id = '72000000-0000-4000-8000-000000000001'),
    0, 'another principal cannot read the manifest');

set local role postgres;
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = '72000000-0000-4000-8000-000000000001'),
    3, 'request creation, acceptance, and manifest freeze are audited');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = '72000000-0000-4000-8000-000000000001'
      and metadata ? 'evidence_text'), 0,
    'deletion request audit metadata contains no user content');

select * from finish();
rollback;
