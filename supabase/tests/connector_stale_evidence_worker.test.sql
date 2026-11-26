-- The service-role connector worker can remove stale connector evidence;
-- every deletion is still audited, and callers without an identity still fail
-- closed (20261127110000).
begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(9);

insert into auth.users (id, aud, role, email) values
    ('e6000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'stale-owner@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'e6000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e6000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
select workspace_id as owner_workspace from public.ensure_personal_workspace() \gset
insert into public.connector_connections (
    connection_id, user_id, workspace_id, provider, access_token_ciphertext
) values (
    'e7000000-0000-4000-8000-000000000001', 'e6000000-0000-4000-8000-000000000001',
    :'owner_workspace'::uuid, 'github', 'v1.ciphertext'
);
select public.upsert_connector_evidence(
    :'owner_workspace'::uuid,
    'e7000000-0000-4000-8000-000000000001',
    jsonb_build_array(
        jsonb_build_object('content', 'kept', 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/kept.md', 'source_label', 'GitHub: acme/docs/kept.md'),
        jsonb_build_object('content', 'deleted file', 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/gone.md', 'source_label', 'GitHub: acme/docs/gone.md'),
        jsonb_build_object('content', 'obsolete part', 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/big.py#part-002',
            'source_label', 'GitHub: acme/docs/big.py #part=2/2 chars=58000-60000'),
        jsonb_build_object('content', 'owner removes this', 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/manual.md', 'source_label', 'GitHub: acme/docs/manual.md')
    )
);

-- Worker (service role, no auth.uid()).
set local role service_role;
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(public.delete_stale_connector_evidence(
    'e7000000-0000-4000-8000-000000000001',
    array['acme/docs/kept.md', 'acme/docs/manual.md']
), 2, 'the worker removes the deleted file and the obsolete part');

set local role postgres;
select is((select array_agg(connector_external_id order by connector_external_id)
    from public.evidence_records
    where connector_connection_id = 'e7000000-0000-4000-8000-000000000001'),
    (select array_agg(id order by id) from unnest(array['acme/docs/kept.md', 'acme/docs/manual.md']) as id),
    'current evidence is kept');
select is((select count(*)::integer from public.evidence_deletion_audit
    where actor_user_id = 'e6000000-0000-4000-8000-000000000001'
      and workspace_id = :'owner_workspace'::uuid
      and metadata = '{"connector_worker": true}'::jsonb), 2,
    'each worker deletion is audited with the connection owner as actor');

-- A second run with nothing stale deletes nothing and does not fail.
set local role service_role;
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(public.delete_stale_connector_evidence(
    'e7000000-0000-4000-8000-000000000001',
    array['acme/docs/kept.md', 'acme/docs/manual.md']
), 0, 'a run with nothing stale deletes nothing');

-- Still fails closed without an identity: no sub and not the service role.
set local role postgres;
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
select throws_ok($$
    delete from public.evidence_records where connector_external_id = 'acme/docs/kept.md'
$$, '42501', 'deletion audit requires an authenticated actor',
    'a delete without an identity is still refused');
select set_config('request.jwt.claims', '', true);
select throws_ok($$
    delete from public.evidence_records where connector_external_id = 'acme/docs/kept.md'
$$, '42501', 'deletion audit requires an authenticated actor',
    'a delete without any claims is still refused');

-- Non-connector evidence gets no service-role fallback.
insert into public.evidence_records (id, workspace_id, content) values
    ('e8000000-0000-4000-8000-000000000001', :'owner_workspace'::uuid, 'manual evidence');
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select throws_ok($$
    delete from public.evidence_records where id = 'e8000000-0000-4000-8000-000000000001'
$$, '42501', 'deletion audit requires an authenticated actor',
    'the service-role fallback applies only to connector evidence');

-- Signed-in owner deletes are unchanged.
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e6000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e6000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
delete from public.evidence_records where connector_external_id = 'acme/docs/manual.md';
set local role postgres;
select is((select metadata from public.evidence_deletion_audit
    where actor_user_id = 'e6000000-0000-4000-8000-000000000001'
    order by occurred_at desc limit 1), '{}'::jsonb,
    'a signed-in delete keeps its plain audit event');
select is((select count(*)::integer from public.evidence_records
    where connector_connection_id = 'e7000000-0000-4000-8000-000000000001'), 1,
    'only the kept file remains');

select * from finish();
rollback;
