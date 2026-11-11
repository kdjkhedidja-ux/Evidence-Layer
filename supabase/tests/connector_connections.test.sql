begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(6);

insert into auth.users (id, aud, role, email) values
    ('c1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'connector-owner@example.test'),
    ('c1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'connector-other@example.test');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'c1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'c1000000-0000-4000-8000-000000000001')::text, true);

insert into public.connector_connections (
    connection_id, user_id, provider, scopes, access_token_ciphertext
) values (
    'c2000000-0000-4000-8000-000000000001',
    'c1000000-0000-4000-8000-000000000001',
    'google_drive', array['drive.readonly'], 'v1.ciphertext'
);
insert into public.connector_documents (
    document_id, user_id, connection_id, provider, external_id, name,
    revision, content_text, content_sha256, size_bytes
) values (
    'c3000000-0000-4000-8000-000000000001',
    'c1000000-0000-4000-8000-000000000001',
    'c2000000-0000-4000-8000-000000000001',
    'google_drive', 'drive-file-1', 'Document',
    'rev-1', 'hello', repeat('a', 64), 5
);

select is((select count(*)::integer from public.connector_connections), 1,
    'the owner can read the connection');
select is((select count(*)::integer from public.connector_documents), 1,
    'the owner can read connector documents');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'c1000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'c1000000-0000-4000-8000-000000000002')::text, true);
select is((select count(*)::integer from public.connector_connections), 0,
    'another user cannot read the connection');
select is((select count(*)::integer from public.connector_documents), 0,
    'another user cannot read connector documents');
select throws_ok($$
    insert into public.connector_connections (
        connection_id, user_id, provider, access_token_ciphertext
    ) values (
        'c2000000-0000-4000-8000-000000000002',
        'c1000000-0000-4000-8000-000000000001',
        'google_drive', 'v1.other'
    )
$$, '42501', null, 'another user cannot insert a connection for the owner');

set local role authenticated;
select set_config('request.jwt.claim.sub', 'c1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    json_build_object('sub', 'c1000000-0000-4000-8000-000000000001')::text, true);
delete from public.connector_connections
 where connection_id = 'c2000000-0000-4000-8000-000000000001';
select is((select count(*)::integer from public.connector_documents), 0,
    'deleting the connection removes its documents');

select * from finish();
rollback;
