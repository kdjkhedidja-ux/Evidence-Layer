-- Large-file parts are ordinary connector evidence rows: workspace isolation
-- (RLS + RPC checks) and every removal path apply to each part.
begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(13);

insert into auth.users (id, aud, role, email) values
    ('e1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'parts-owner@example.test'),
    ('e1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'parts-other@example.test');

-- Owner: personal workspace, a GitHub and a second connection, one large file as 3 parts
-- plus one small single-record file.
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
select workspace_id as owner_workspace from public.ensure_personal_workspace() \gset

insert into public.connector_connections (
    connection_id, user_id, workspace_id, provider, access_token_ciphertext
) values
    ('e2000000-0000-4000-8000-000000000001', 'e1000000-0000-4000-8000-000000000001',
     :'owner_workspace'::uuid, 'github', 'v1.ciphertext'),
    ('e2000000-0000-4000-8000-000000000002', 'e1000000-0000-4000-8000-000000000001',
     :'owner_workspace'::uuid, 'notion', 'v1.ciphertext');

select is(public.upsert_connector_evidence(
    :'owner_workspace'::uuid,
    'e2000000-0000-4000-8000-000000000001',
    jsonb_build_array(
        jsonb_build_object('content', repeat('a', 60000), 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/app.py#part-001',
            'source_label', 'GitHub: acme/docs/app.py #part=1/3 chars=0-60000'),
        jsonb_build_object('content', repeat('b', 60000), 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/app.py#part-002',
            'source_label', 'GitHub: acme/docs/app.py #part=2/3 chars=58000-118000'),
        jsonb_build_object('content', repeat('c', 10000), 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/app.py#part-003',
            'source_label', 'GitHub: acme/docs/app.py #part=3/3 chars=116000-126000'),
        jsonb_build_object('content', 'small file', 'connector_revision', 'r1',
            'connector_external_id', 'acme/docs/README.md',
            'source_label', 'GitHub: acme/docs/README.md')
    )
), 4, 'the owner writes three parts and one single record');
select is((select count(*)::integer from public.evidence_records
    where connector_connection_id = 'e2000000-0000-4000-8000-000000000001'
      and connector_external_id like '%#part-%'), 3, 'the owner reads every part');

-- Another user: no read, no write into the owner's workspace or connection.
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000002', 'role', 'authenticated')::text, true);
select workspace_id as other_workspace from public.ensure_personal_workspace() \gset
select is((select count(*)::integer from public.evidence_records
    where connector_connection_id = 'e2000000-0000-4000-8000-000000000001'), 0,
    'another user cannot read the owner''s parts');
select is((select count(*)::integer from public.evidence_records
    where workspace_id = :'owner_workspace'::uuid), 0,
    'another user sees nothing in the owner workspace');
select throws_ok(format($$
    select public.upsert_connector_evidence(%L::uuid, 'e2000000-0000-4000-8000-000000000001',
        '[{"content":"x","connector_revision":"r","connector_external_id":"acme/docs/app.py#part-004","source_label":"GitHub: x"}]'::jsonb)
$$, :'owner_workspace'), '42501', null, 'another user cannot add a part to the owner connection');
select throws_ok(format($$
    select public.upsert_connector_evidence(%L::uuid, 'e2000000-0000-4000-8000-000000000001',
        '[{"content":"x","connector_revision":"r","connector_external_id":"acme/docs/app.py#part-004","source_label":"GitHub: x"}]'::jsonb)
$$, :'other_workspace'), '42501', null, 'another user cannot write the owner connection into their own workspace');

-- Worker (service role): writes parts for the second connection as the
-- connection owner.
set local role service_role;
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(public.upsert_connector_evidence(
    :'owner_workspace'::uuid,
    'e2000000-0000-4000-8000-000000000002',
    jsonb_build_array(
        jsonb_build_object('content', repeat('d', 60000), 'connector_revision', 'r1',
            'connector_external_id', 'acme/other/big.md#part-001',
            'source_label', 'GitHub: acme/other/big.md #part=1/2 chars=0-60000'),
        jsonb_build_object('content', repeat('e', 9000), 'connector_revision', 'r1',
            'connector_external_id', 'acme/other/big.md#part-002',
            'source_label', 'GitHub: acme/other/big.md #part=2/2 chars=58000-67000')
    )
), 2, 'the worker writes parts as the connection owner');

-- Obsolete parts (the file shrank) are removed through the owner session
-- path of delete_stale_connector_evidence (connector_repository.py).
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
delete from public.evidence_records
 where connector_connection_id = 'e2000000-0000-4000-8000-000000000001'
   and connector_external_id in ('acme/docs/app.py#part-002', 'acme/docs/app.py#part-003');
set local role postgres;
select is((select count(*)::integer from public.evidence_deletion_audit
    where actor_user_id = 'e1000000-0000-4000-8000-000000000001'), 2,
    'each removed part is audited with the owner as actor');

set local role postgres;
select is((select array_agg(connector_external_id order by connector_external_id)
    from public.evidence_records
    where connector_connection_id = 'e2000000-0000-4000-8000-000000000001'),
    (select array_agg(id order by id) from unnest(array['acme/docs/README.md', 'acme/docs/app.py#part-001']) as id),
    'only the current part and the small file remain');
select is((select workspace_id from public.evidence_records
    where connector_external_id = 'acme/other/big.md#part-001'),
    :'owner_workspace'::uuid, 'worker-written parts land in the owner workspace');

-- Disconnect: deleting the connection removes all of its parts.
set local role authenticated;
select set_config('request.jwt.claim.sub', 'e1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'e1000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
delete from public.connector_connections
 where connection_id = 'e2000000-0000-4000-8000-000000000001';
select is((select count(*)::integer from public.evidence_records
    where connector_connection_id = 'e2000000-0000-4000-8000-000000000001'), 0,
    'disconnecting the source removes its parts');

-- Account deletion: the remaining connection's parts go with the account.
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    'e5000000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001', 'account',
    'e1000000-0000-4000-8000-000000000001', 'parts-purge-key'
);
set local role account_lifecycle_transition_writer;
update public.data_deletion_requests
   set status = 'accepted', accepted_at = clock_timestamp()
 where request_id = 'e5000000-0000-4000-8000-000000000001';
update public.account_lifecycle
   set state = 'revocation_pending', request_id = 'e5000000-0000-4000-8000-000000000001'
 where subject_id = 'e1000000-0000-4000-8000-000000000001';
set local role account_deletion_worker;
select ok((private.run_account_deletion_slice('e5000000-0000-4000-8000-000000000001')
    ->> 'status') = 'purge_pending', 'the account deletion slice completes');
select private.prepare_individual_account_purge_v3('e5000000-0000-4000-8000-000000000001');
select private.delete_account_lifecycle_for_purge('e5000000-0000-4000-8000-000000000001');
set local role postgres;
delete from auth.users where id = 'e1000000-0000-4000-8000-000000000001';
select is((select count(*)::integer from public.evidence_records
    where connector_external_id like 'acme/other/big.md#part-%'
       or workspace_id = :'owner_workspace'::uuid), 0,
    'account deletion removes every remaining part');

select * from finish();
rollback;
