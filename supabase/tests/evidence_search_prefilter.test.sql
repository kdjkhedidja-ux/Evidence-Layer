-- Step E (20261127120000): retrieval candidates come from the whole workspace.
-- An old record outside the 1000 newest must be reachable through both the
-- signed-in search and the signed API-key/MCP candidate read, with workspace
-- isolation and the gateway proof unchanged.
begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(13);

insert into auth.users (id, aud, role, email) values
    ('e9000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'search-owner@example.test'),
    ('e9000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'search-other@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e9000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e9000000-0000-4000-8000-000000000002', 'role', 'authenticated')::text, true);
select workspace_id as other_workspace from public.ensure_personal_workspace() \gset
select set_config('request.jwt.claim.sub', 'e9000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e9000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
select workspace_id as owner_workspace from public.ensure_personal_workspace() \gset

set local role postgres;
-- The answer is the OLDEST record; 1004 newer records do not mention it.
insert into public.evidence_records (id, workspace_id, content, source_label, created_at, updated_at)
values (
    'ea000000-0000-4000-8000-000000000001', :'owner_workspace'::uuid,
    'Zebrafish ledger reconciliation runs nightly and closes the books.',
    'GitHub: acme/docs/finance/ledger.md',
    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
);
insert into public.evidence_records (workspace_id, content, source_label, created_at, updated_at)
select :'owner_workspace'::uuid,
       'Routine status note number ' || n || ' about deployments and builds.',
       'GitHub: acme/docs/notes/status-' || n || '.md',
       '2026-06-01T00:00:00Z'::timestamptz + n * interval '1 minute',
       '2026-06-01T00:00:00Z'::timestamptz + n * interval '1 minute'
  from generate_series(1, 1004) as n;
-- Another workspace has a matching record that must never be returned.
insert into public.evidence_records (id, workspace_id, content, created_at, updated_at)
values ('ea000000-0000-4000-8000-000000000002', :'other_workspace'::uuid,
        'Zebrafish ledger reconciliation of another tenant.', now(), now());

select ok((select count(*) from public.evidence_records
    where workspace_id = :'owner_workspace'::uuid
      and created_at > '2026-01-01T00:00:00Z') >= 1000,
    'the answer is outside the 1000 newest records of the workspace');
select has_index('public', 'evidence_records', 'evidence_records_search_idx',
    'the full-text prefilter index exists');

-- Signed-in path (SECURITY INVOKER, caller's RLS).
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e9000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e9000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
select is((select array_agg(id) from public.search_workspace_evidence(
        :'owner_workspace'::uuid, 'How does the zebrafish ledger reconciliation work?', 200)),
    array['ea000000-0000-4000-8000-000000000001'::uuid],
    'the owner finds the old record through the workspace prefilter');
select is((select count(*)::integer from public.search_workspace_evidence(
        :'owner_workspace'::uuid, 'the of and', 200)), 0,
    'a query of stop words only matches nothing and does not fail');
select throws_ok(format($$select * from public.search_workspace_evidence(%L::uuid, 'ledger', 501)$$,
    :'owner_workspace'), '22023', 'invalid evidence search', 'the search limit is bounded');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e9000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e9000000-0000-4000-8000-000000000002', 'role', 'authenticated')::text, true);
select is((select count(*)::integer from public.search_workspace_evidence(
        :'owner_workspace'::uuid, 'zebrafish ledger reconciliation', 200)), 0,
    'another user cannot search the owner workspace');

-- Signed API-key / Remote MCP path: one call, newest window + matches.
set local role postgres;
select private.configure_personal_api_key_gateway_secret(repeat('ab', 32));
insert into public.personal_api_keys (
    id, user_id, workspace_id, name, key_prefix, key_hash, scopes, expires_at
) values (
    'eb000000-0000-4000-8000-000000000001', 'e9000000-0000-4000-8000-000000000001',
    :'owner_workspace'::uuid, 'search test', 'evl_pk_0000abcd', repeat('c', 64),
    array['evidence:read', 'relationships:read'], now() + interval '30 days'
);

create function pg_temp.sign(p_nonce uuid, p_limit integer) returns void language plpgsql as $sign$
declare
    v_ts text := floor(extract(epoch from clock_timestamp()))::bigint::text;
    v_message text;
begin
    v_message := concat_ws(E'\n', 'v1', 'evidence', 'eb000000-0000-4000-8000-000000000001',
        current_setting('test.workspace'), repeat('c', 64), v_ts, p_nonce::text, p_limit::text, '', '');
    perform set_config('request.headers', json_build_object(
        'x-evidence-api-key-hash', repeat('c', 64),
        'x-evidence-api-key-gateway-timestamp', v_ts,
        'x-evidence-api-key-gateway-nonce', p_nonce::text,
        'x-evidence-api-key-gateway-proof', encode(extensions.hmac(
            convert_to(v_message, 'UTF8'), decode(repeat('ab', 32), 'hex'), 'sha256'), 'hex')
    )::text, true);
end;
$sign$;
grant execute on function pg_temp.sign(uuid, integer) to anon;
select set_config('test.workspace', :'owner_workspace', true);

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select pg_temp.sign('ec000000-0000-4000-8000-000000000001', 1001);
create temp table api_rows on commit drop as
select * from public.read_personal_api_key_evidence_candidates(
    'eb000000-0000-4000-8000-000000000001', :'owner_workspace'::uuid, 1001,
    'How does the zebrafish ledger reconciliation work?', 200);
select is((select count(*)::integer from api_rows where id = 'ea000000-0000-4000-8000-000000000001'), 1,
    'the signed candidate read returns the old record outside the newest window');
select is((select count(*)::integer from api_rows), 1002,
    'one signed call returns the 1001 newest records plus the match');
select is((select count(*)::integer from api_rows where workspace_id <> :'owner_workspace'::uuid), 0,
    'the signed read never returns another workspace');

select throws_ok($$
    select * from public.read_personal_api_key_evidence_candidates(
        'eb000000-0000-4000-8000-000000000001', current_setting('test.workspace')::uuid, 1001,
        'zebrafish', 200)
$$, '28000', null, 'a replayed nonce is refused');
select pg_temp.sign('ec000000-0000-4000-8000-000000000002', 1001);
select throws_ok($$
    select * from public.read_personal_api_key_evidence_candidates(
        'eb000000-0000-4000-8000-000000000001', current_setting('test.workspace')::uuid, 1000,
        'zebrafish', 200)
$$, '28000', null, 'a proof for another limit is refused');

set local role postgres;
select ok(not has_function_privilege('authenticated',
    'public.read_personal_api_key_evidence_candidates(uuid,uuid,integer,text,integer)', 'EXECUTE')
    and not has_function_privilege('anon', 'public.search_workspace_evidence(uuid,text,integer)', 'EXECUTE'),
    'each entry point is reachable only from its own path');
select is((select pg_get_userbyid(proowner)::text from pg_proc
    where oid = 'public.read_personal_api_key_evidence_candidates(uuid,uuid,integer,text,integer)'::regprocedure),
    'personal_api_key_reader', 'the signed read runs as the personal API key reader');

select * from finish();
rollback;
