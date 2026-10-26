begin;

create extension if not exists pgtap with schema extensions;

select plan(68);

-- Every policy on storage.objects is pinned by NAME, target relation, command,
-- permissive/restrictive kind and roles. A bare count would stay green if one
-- policy were renamed or swapped for another of the same shape, so each policy
-- is asserted individually. pg_temp.policy_signature renders
-- "<cmd>:<kind>:<roles>" and returns NULL when the policy is missing, so a
-- rename or a migration to another relation fails the assertion.
create function pg_temp.policy_signature(p_relation regclass, p_policy name)
returns text
language sql stable as $$
    select policy.polcmd::text
           || ':' || case when policy.polpermissive then 'permissive' else 'restrictive' end
           || ':' || (
                  select string_agg(
                             case role_oid when 0 then 'PUBLIC' else pg_get_userbyid(role_oid) end,
                             ',' order by 1)
                  from unnest(policy.polroles) as role_oid)
    from pg_catalog.pg_policy as policy
    where policy.polrelid = p_relation
      and policy.polname = p_policy
$$;

select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_insert_staged_by_owner'),
    'a:permissive:authenticated',
    'storage policy source_snapshots_objects_insert_staged_by_owner is an authenticated-only INSERT policy'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_select_granted_published'),
    'r:permissive:authenticated',
    'storage policy source_snapshots_objects_select_granted_published is an authenticated-only SELECT policy'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_select_allowlisted_verifier'),
    'r:permissive:authenticated',
    'storage policy source_snapshots_objects_select_allowlisted_verifier is an authenticated-only SELECT policy'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_select_publisher_owner'),
    'r:permissive:snapshot_publisher_owner',
    'storage policy source_snapshots_objects_select_publisher_owner is a publisher-owner-only SELECT policy'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_verifier_role_gate_restrictive_fence'),
    'r:restrictive:PUBLIC',
    'storage policy source_snapshots_objects_verifier_role_gate_restrictive_fence is a PUBLIC restrictive role gate'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_select_authenticated_restrictive_fence'),
    'r:restrictive:authenticated',
    'storage policy source_snapshots_objects_select_authenticated_restrictive_fence is an authenticated restrictive fence'
);
select is(
    pg_temp.policy_signature('storage.objects', 'source_snapshots_objects_publisher_owner_restrictive_fence'),
    'r:restrictive:snapshot_publisher_owner',
    'storage policy source_snapshots_objects_publisher_owner_restrictive_fence is a publisher-owner restrictive fence'
);

-- Structure -----------------------------------------------------------------
select ok(
    (select not public and file_size_limit = 8388608
            and allowed_mime_types = array['application/octet-stream']
     from storage.buckets where id = 'source-snapshots'),
    'the source-snapshots bucket is private, size-limited and octet-stream only'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'storage' and tablename = 'objects'
       and policyname like 'source_snapshots_objects_%'),
    7, 'the snapshot object policies include the publisher fence and only the expected readers and uploader'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'storage' and tablename = 'objects'
       and policyname like 'source_snapshots_objects_%'
       and cmd not in ('INSERT', 'SELECT')),
    0, 'the snapshot policies are INSERT and SELECT only'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'storage' and tablename = 'objects'
       and cmd in ('UPDATE', 'DELETE', 'ALL')
       and (coalesce(qual, '') like '%source-snapshots%'
            or coalesce(with_check, '') like '%source-snapshots%')),
    0, 'no UPDATE, DELETE or ALL policy reaches the source-snapshots bucket'
);
select ok(
    (select relrowsecurity from pg_class where oid = 'storage.objects'::regclass),
    'RLS is enabled on storage.objects'
);

-- Bucket helper: creates once, leaves compatible configuration untouched, and
-- rejects every conflicting field. These rows are inside this test transaction.
create temporary table snapshot_bucket_fixture as
select 'snapshot-verifier-test-' || txid_current()::text as id;
select lives_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture),
        (select id from snapshot_bucket_fixture),
        false, 123, array['application/octet-stream']::text[]
    ) $q$,
    'a missing test bucket is created with the requested configuration'
);
select ok(
    (select count(*) = 1 and bool_and(
        name = id and not public and file_size_limit = 123
        and allowed_mime_types = array['application/octet-stream']::text[]
    ) from storage.buckets where id = (select id from snapshot_bucket_fixture)),
    'new bucket name, visibility, size and MIME settings match the request'
);
select lives_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture),
        (select id from snapshot_bucket_fixture),
        false, 123, array['application/octet-stream']::text[]
    ) $q$,
    'rerunning with a compatible bucket succeeds without an update'
);
select ok(
    (select count(*) = 1 and bool_and(
        name = id and not public and file_size_limit = 123
        and allowed_mime_types = array['application/octet-stream']::text[]
    ) from storage.buckets where id = (select id from snapshot_bucket_fixture)),
    'compatible rerun preserves the bucket configuration'
);
select throws_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture), 'different-name',
        false, 123, array['application/octet-stream']::text[]
    ) $q$,
    '55000', 'snapshot bucket has conflicting name or security settings',
    'a conflicting bucket name fails closed'
);
select throws_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture),
        (select id from snapshot_bucket_fixture),
        true, 123, array['application/octet-stream']::text[]
    ) $q$,
    '55000', 'snapshot bucket has conflicting name or security settings',
    'a public bucket conflicts with the private requirement'
);
select throws_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture),
        (select id from snapshot_bucket_fixture),
        false, 124, array['application/octet-stream']::text[]
    ) $q$,
    '55000', 'snapshot bucket has conflicting name or security settings',
    'a different size limit fails closed'
);
select throws_ok(
    $q$ select private.ensure_snapshot_bucket(
        (select id from snapshot_bucket_fixture),
        (select id from snapshot_bucket_fixture),
        false, 123, array['text/plain']::text[]
    ) $q$,
    '55000', 'snapshot bucket has conflicting name or security settings',
    'a different MIME allowlist fails closed'
);
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'snapshot-name-source-' || txid_current()::text,
    'snapshot-name-collision-' || txid_current()::text,
    false, 123, array['application/octet-stream']::text[]
);
select throws_ok(
    $q$ select private.ensure_snapshot_bucket(
        'snapshot-name-target-' || txid_current()::text,
        'snapshot-name-collision-' || txid_current()::text,
        false, 123, array['application/octet-stream']::text[]
    ) $q$,
    '55000', 'snapshot bucket name is already used by another bucket',
    'a bucket name already owned by another id fails clearly'
);

-- Fixtures ------------------------------------------------------------------
insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'owner-a@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'granted@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'ungranted@example.test'),
    ('40000000-0000-4000-8000-000000000004', 'authenticated', 'authenticated', 'inactive@example.test'),
    ('50000000-0000-4000-8000-000000000005', 'authenticated', 'authenticated', 'owner-b@example.test'),
    ('60000000-0000-4000-8000-000000000006', 'authenticated', 'authenticated', 'verifier-a@example.test');

insert into public.workspaces (id, name)
values
    ('a1000000-0000-4000-8000-000000000001', 'A'),
    ('b3000000-0000-4000-8000-000000000003', 'B');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', 'member', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000003', 'member', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000004', 'member', 'inactive'),
    ('b3000000-0000-4000-8000-000000000003', '50000000-0000-4000-8000-000000000005', 'owner', 'active');

-- Grants for the granted member and the (inactive) fourth member.
insert into public.source_grants (workspace_id, source_id, principal_id, grant_type, granted_by)
values
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
     '20000000-0000-4000-8000-000000000002', 'read', '10000000-0000-4000-8000-000000000001'),
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
     '40000000-0000-4000-8000-000000000004', 'read', '10000000-0000-4000-8000-000000000001');

-- v1, v2, v3: staged by the owner of A. v9: staged by the owner of B.
insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by)
select 'a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', v.id::uuid,
       'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/' || v.id,
       repeat('a', 64), 100, 'staged', '10000000-0000-4000-8000-000000000001'
from (values
    ('d1000000-0000-4000-8000-000000000001'),
    ('d2000000-0000-4000-8000-000000000002'),
    ('d3000000-0000-4000-8000-000000000003')
) as v(id);
insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by)
values ('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002',
        'd9000000-0000-4000-8000-000000000009',
        'workspace/b3000000-0000-4000-8000-000000000003/source/c2000000-0000-4000-8000-000000000002/version/d9000000-0000-4000-8000-000000000009',
        repeat('a', 64), 100, 'staged', '50000000-0000-4000-8000-000000000005');

create temporary table obs (name text primary key, value text not null);
grant insert, select on obs to authenticated, snapshot_verifier;

create function pg_temp.try(name text, stmt text) returns void
language plpgsql as $$
declare n integer;
begin
    execute stmt;
    get diagnostics n = row_count;
    insert into pg_temp.obs values (name, n::text);
exception when others then
    insert into pg_temp.obs values (name, sqlstate);
end $$;

create function pg_temp.cnt(name text, q text) returns void
language plpgsql as $$
declare n integer;
begin
    execute 'select count(*) from (' || q || ') as sub' into n;
    insert into pg_temp.obs values (name, n::text);
end $$;

-- Object insert as Storage would issue it, for a given key suffix and bucket.
create function pg_temp.put(key text, bucket text default 'source-snapshots') returns text
language sql as $$
    select format($f$insert into storage.objects (bucket_id, name) values (%L, %L)$f$, bucket, key)
$$;

-- Over-grant DML only inside this rollback-only transaction so the denials
-- below come from the absence of UPDATE/DELETE policies, not of table grants.
grant update, delete on storage.objects to authenticated;

-- Owner of workspace A uploads only into keys they staged ---------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('owner_upload_d1', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001'));
select pg_temp.try('owner_upload_d2', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d2000000-0000-4000-8000-000000000002'));
select pg_temp.try('owner_upload_without_record', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d4000000-0000-4000-8000-000000000004'));
select pg_temp.try('owner_upload_into_other_workspace', pg_temp.put('workspace/b3000000-0000-4000-8000-000000000003/source/c2000000-0000-4000-8000-000000000002/version/d9000000-0000-4000-8000-000000000009'));
select pg_temp.try('owner_upload_wrong_bucket_path', pg_temp.put('../escape/d3000000-0000-4000-8000-000000000003'));
select pg_temp.cnt('owner_reads_staged_objects', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);

-- Another member / another owner cannot upload into someone else's staged key.
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('member_upload_staged_key', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d3000000-0000-4000-8000-000000000003'));

reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-4000-8000-000000000005', true);
select set_config('request.jwt.claims',
    '{"sub":"50000000-0000-4000-8000-000000000005","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('other_owner_upload_a_key', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d3000000-0000-4000-8000-000000000003'));

-- Staged (orphan-until-published) objects are unreadable to every client -------
reset role;
-- Record what Storage would: the uploader and the stored size.
update storage.objects
set owner_id = '10000000-0000-4000-8000-000000000001', metadata = jsonb_build_object('size', 100),
    version = 'ver-' || name
where bucket_id = 'source-snapshots' and name like 'workspace/a1000000-0000-4000-8000-000000000001/%';
-- An object with no catalog record at all.
insert into storage.objects (bucket_id, name, owner_id, metadata)
values ('source-snapshots',
        'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d7000000-0000-4000-8000-000000000007',
        '10000000-0000-4000-8000-000000000001', '{"size": 100}');

set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('granted_reads_before_publish', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);

reset role;
-- A verifier identity scoped to workspace A (operator step, rollback-only here).
-- It is an ordinary `authenticated` session; the allowlist and the RPC decide.
-- RLS is FORCED on the allowlist, so the seeding session acts as the allowlist
-- owner and temporarily drops FORCE, then restores it immediately.
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
alter table private.snapshot_verifier_allowlist no force row level security;
insert into private.snapshot_verifier_allowlist (user_id, workspace_id)
values ('60000000-0000-4000-8000-000000000006', 'a1000000-0000-4000-8000-000000000001');
alter table private.snapshot_verifier_allowlist force row level security;
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims',
    '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('verifier_reads_cataloged_objects', $q$
    select 1 from storage.objects where bucket_id = 'source-snapshots' and name like 'workspace/a1000000-0000-4000-8000-000000000001/%'
$q$);
select pg_temp.try('verifier_upload', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d3000000-0000-4000-8000-000000000003'));
select pg_temp.try('verifier_publish_d1', $q$
    select public.publish_verified_snapshot(
        'a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100,
        (select id from storage.objects where bucket_id = 'source-snapshots'
            and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001'),
        (select version from storage.objects where bucket_id = 'source-snapshots'
            and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001'))
$q$);
reset role;

-- Published object: only active + granted members can read it -----------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('granted_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
select pg_temp.cnt('granted_reads_unpublished_staged', $q$
    select 1 from storage.objects where name like '%d2000000-0000-4000-8000-000000000002'
$q$);
select pg_temp.try('granted_update_object', $q$
    update storage.objects set name = name || 'x' where bucket_id = 'source-snapshots'
$q$);
select pg_temp.try('granted_delete_object', $q$
    delete from storage.objects where bucket_id = 'source-snapshots'
$q$);
select pg_temp.try('granted_upload_over_published', pg_temp.put('workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001'));

reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('ungranted_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);

reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '40000000-0000-4000-8000-000000000004', true);
select set_config('request.jwt.claims',
    '{"sub":"40000000-0000-4000-8000-000000000004","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('inactive_granted_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);

reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-4000-8000-000000000005', true);
select set_config('request.jwt.claims',
    '{"sub":"50000000-0000-4000-8000-000000000005","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('other_workspace_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);

reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('owner_without_grant_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
select pg_temp.try('owner_update_object', $q$
    update storage.objects set name = name || 'x' where bucket_id = 'source-snapshots'
$q$);
select pg_temp.try('owner_delete_object', $q$
    delete from storage.objects where bucket_id = 'source-snapshots'
$q$);
-- Revoke the grant: the member loses access immediately.
select pg_temp.try('owner_revokes_grant', $q$
    delete from public.source_grants where principal_id = '20000000-0000-4000-8000-000000000002'
$q$);
reset role;

set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('revoked_member_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
reset role;

-- Deactivating the membership also closes access, grant or not.
insert into public.source_grants (workspace_id, source_id, principal_id, grant_type, granted_by)
values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000002', 'read', '10000000-0000-4000-8000-000000000001');
update public.workspace_memberships set status = 'inactive'
where user_id = '20000000-0000-4000-8000-000000000002';
set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('deactivated_member_reads_published', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
reset role;

select is((select value from obs where name = 'owner_upload_d1'), '1', 'owner can upload into a key they staged');
select is((select value from obs where name = 'owner_upload_d2'), '1', 'owner can upload a second staged key');
select is((select value from obs where name = 'owner_upload_without_record'), '42501', 'uploading with no staged catalog record is denied');
select is((select value from obs where name = 'owner_upload_into_other_workspace'), '42501', 'owner of A cannot upload into workspace B''s staged key');
select is((select value from obs where name = 'owner_upload_wrong_bucket_path'), '42501', 'a path that matches no staged record is denied');
select is((select value from obs where name = 'owner_reads_staged_objects'), '0', 'the uploader cannot read their own unpublished object');
select is((select value from obs where name = 'member_upload_staged_key'), '42501', 'a non-owner member cannot upload into a staged key');
select is((select value from obs where name = 'other_owner_upload_a_key'), '42501', 'another workspace owner cannot upload into a staged key');
select is((select value from obs where name = 'granted_reads_before_publish'), '0', 'a granted member cannot read staged or orphaned objects');
select is((select value from obs where name = 'verifier_reads_cataloged_objects'), '2', 'the verifier reads cataloged objects only, not uncataloged orphans');
select is((select value from obs where name = 'verifier_upload'), '42501', 'the verifier cannot upload');
select is((select value from obs where name = 'verifier_publish_d1'), '1', 'the verifier can publish after the object is stored');
select is((select value from obs where name = 'granted_reads_published'), '1', 'a granted active member reads only the published object');
select is((select value from obs where name = 'granted_reads_unpublished_staged'), '0', 'a granted member cannot read a staged sibling version');
select is((select value from obs where name = 'granted_update_object'), '0', 'no policy allows updating an object, even over-granted');
select ok((select value from obs where name = 'granted_delete_object') in ('0', '42501'), 'deleting an object is denied (no policy; Storage also blocks direct deletes)');
select is((select value from obs where name = 'granted_upload_over_published'), '42501', 'a key cannot be re-uploaded once published');
select is((select value from obs where name = 'ungranted_reads_published'), '0', 'a member without a grant cannot read');
select is((select value from obs where name = 'inactive_granted_reads_published'), '0', 'an inactive member with a grant cannot read');
select is((select value from obs where name = 'other_workspace_reads_published'), '0', 'another workspace cannot read');
select is((select value from obs where name = 'owner_without_grant_reads_published'), '0', 'ownership alone does not allow reading object bytes');
select is((select value from obs where name = 'owner_update_object'), '0', 'the owner cannot update objects');
select ok((select value from obs where name = 'owner_delete_object') in ('0', '42501'), 'the owner cannot delete objects');
select is((select value from obs where name = 'owner_revokes_grant'), '1', 'the owner can revoke a grant');
select is((select value from obs where name = 'revoked_member_reads_published'), '0', 'revoking the grant removes object access');
select is((select value from obs where name = 'deactivated_member_reads_published'), '0', 'deactivating the membership removes object access');
select is(
    (select count(*)::integer from storage.objects
     where bucket_id = 'source-snapshots' and name like 'workspace/a1000000-0000-4000-8000-000000000001/%'),
    3, 'no object was removed or replaced by the denied attempts'
);
select is(
    (select count(distinct name)::integer from storage.objects
     where bucket_id = 'source-snapshots' and name like 'workspace/a1000000-0000-4000-8000-000000000001/%'),
    3, 'no object name was altered by the denied attempts'
);
select ok(
    not exists (select 1 from storage.objects
                where bucket_id = 'source-snapshots' and name like 'workspace/a1000000-0000-4000-8000-000000000001/%' and name like '%x'),
    'no object was renamed by the denied attempts'
);
select is(
    (select count(*)::integer from storage.objects
     where bucket_id = 'source-snapshots'
       and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d7000000-0000-4000-8000-000000000007'),
    1, 'the uncataloged orphan object remains stored but is readable by no API role'
);

-- Simulate legacy published records with one missing binding field each. This
-- is intentionally metadata-only and transaction-local; it does not claim to
-- exercise Storage API behavior or manufacture an attestation for a real row.
reset role;
update public.workspace_memberships
set status = 'active'
where user_id = '20000000-0000-4000-8000-000000000002';
alter table public.source_snapshots
    drop constraint source_snapshots_published_verifier_binding;
alter table public.source_snapshots
    disable trigger source_snapshots_protect_immutability;
alter table public.source_snapshots
    disable trigger source_snapshots_require_verification_binding;

insert into storage.objects (bucket_id, name, owner_id, metadata, version)
values
    ('source-snapshots', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da100000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', '{"size": 100}', 'legacy-object-ver-1'),
    ('source-snapshots', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da200000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', '{"size": 100}', 'legacy-object-ver-2'),
    ('source-snapshots', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da300000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001', '{"size": 100}', 'legacy-object-ver-3');

create temporary table legacy_read_objects as
select name, id, version
from storage.objects
where name like '%/version/da100000-0000-4000-8000-000000000001'
   or name like '%/version/da200000-0000-4000-8000-000000000002'
   or name like '%/version/da300000-0000-4000-8000-000000000003';

insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes,
    state, created_by, published_at, verified_sha256, verified_size_bytes,
    verified_at, verified_by, storage_object_id, storage_object_version
)
values
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'da100000-0000-4000-8000-000000000001', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da100000-0000-4000-8000-000000000001', repeat('a', 64), 100, 'published', '10000000-0000-4000-8000-000000000001', now(), repeat('a', 64), 100, now(), null, (select id from legacy_read_objects where name like '%/version/da100000-0000-4000-8000-000000000001'), 'legacy-object-ver-1'),
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'da200000-0000-4000-8000-000000000002', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da200000-0000-4000-8000-000000000002', repeat('a', 64), 100, 'published', '10000000-0000-4000-8000-000000000001', now(), repeat('a', 64), 100, now(), '60000000-0000-4000-8000-000000000006', null, 'legacy-object-ver-2'),
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'da300000-0000-4000-8000-000000000003', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da300000-0000-4000-8000-000000000003', repeat('a', 64), 100, 'published', '10000000-0000-4000-8000-000000000001', now(), repeat('a', 64), 100, now(), '60000000-0000-4000-8000-000000000006', (select id from legacy_read_objects where name like '%/version/da300000-0000-4000-8000-000000000003'), null);

alter table public.source_snapshots
    enable trigger source_snapshots_protect_immutability;
alter table public.source_snapshots
    enable trigger source_snapshots_require_verification_binding;
alter table public.source_snapshots
    add constraint source_snapshots_published_verifier_binding check (
        state <> 'published'
        or (
            verified_by is not null
            and storage_object_id is not null
            and storage_object_version is not null
            and length(storage_object_version) between 1 and 512
        )
    ) not valid;

set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('legacy_missing_verifier_catalog', $q$
    select 1 from public.source_snapshots where version_id = 'da100000-0000-4000-8000-000000000001'
$q$);
select pg_temp.cnt('legacy_missing_object_id_catalog', $q$
    select 1 from public.source_snapshots where version_id = 'da200000-0000-4000-8000-000000000002'
$q$);
select pg_temp.cnt('legacy_missing_object_version_catalog', $q$
    select 1 from public.source_snapshots where version_id = 'da300000-0000-4000-8000-000000000003'
$q$);
select pg_temp.cnt('legacy_missing_verifier_storage', $q$
    select 1 from storage.objects where name like '%/version/da100000-0000-4000-8000-000000000001'
$q$);
select pg_temp.cnt('legacy_missing_object_id_storage', $q$
    select 1 from storage.objects where name like '%/version/da200000-0000-4000-8000-000000000002'
$q$);
select pg_temp.cnt('legacy_missing_object_version_storage', $q$
    select 1 from storage.objects where name like '%/version/da300000-0000-4000-8000-000000000003'
$q$);
reset role;

select is((select value from obs where name = 'legacy_missing_verifier_catalog'), '0', 'a legacy published row without verified_by is hidden from catalog readers');
select is((select value from obs where name = 'legacy_missing_object_id_catalog'), '0', 'a legacy published row without storage_object_id is hidden from catalog readers');
select is((select value from obs where name = 'legacy_missing_object_version_catalog'), '0', 'a legacy published row without storage_object_version is hidden from catalog readers');
select is((select value from obs where name = 'legacy_missing_verifier_storage'), '0', 'Storage readers cannot read bytes for a legacy row without verified_by');
select is((select value from obs where name = 'legacy_missing_object_id_storage'), '0', 'Storage readers cannot read bytes for a legacy row without storage_object_id');
select is((select value from obs where name = 'legacy_missing_object_version_storage'), '0', 'Storage readers cannot read bytes for a legacy row without storage_object_version');
select throws_ok(
    $q$ update public.source_snapshots
        set state = 'staged'
        where version_id = 'da100000-0000-4000-8000-000000000001' $q$,
    '42501', 'source snapshot versions are immutable once cataloged',
    'a legacy published row is not downgraded to staged to satisfy the new binding constraint'
);

-- The v2 inventory guard is owned by the NOLOGIN allowlist owner and is not
-- executable by the migration actor by design. The drift assertions therefore
-- impersonate the owning identity, exactly as the SECURITY DEFINER callers do in
-- production. The role switch is transactional and is reverted with the test
-- transaction (and with every throws_ok subtransaction).
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
create function pg_temp.inventory_drift() returns void
language plpgsql as $$
begin
    execute 'set role snapshot_verifier_allowlist_owner';
    perform private.assert_snapshot_read_policy_inventory_v2(true);
end $$;

-- As with the catalog relation, a known fence name cannot legitimize a widened
-- USING clause. The policy change and guard call share the DO statement.
select throws_ok(
    $q$ do $test$
    begin
        alter policy source_snapshots_objects_verifier_role_gate_restrictive_fence
            on storage.objects using (true);
        alter policy source_snapshots_objects_select_authenticated_restrictive_fence
            on storage.objects using (true);
        alter policy source_snapshots_objects_publisher_owner_restrictive_fence
            on storage.objects using (true);
        execute 'set role snapshot_verifier_allowlist_owner';
        perform private.assert_snapshot_read_policy_inventory_v2(true);
        raise exception using errcode = 'P0001',
            message = 'test sentinel: guard accepted a widened Storage fence';
    end;
    $test$ $q$,
    '55000', 'snapshot read restrictive fence definition changed',
    'the guard rejects known Storage RESTRICTIVE policies widened to USING (true)'
);
select is(
    (select count(*)::integer from pg_catalog.pg_policy as policy
     where policy.polrelid = 'storage.objects'::pg_catalog.regclass
       and policy.polname in (
           'source_snapshots_objects_verifier_role_gate_restrictive_fence',
           'source_snapshots_objects_select_authenticated_restrictive_fence',
           'source_snapshots_objects_publisher_owner_restrictive_fence')
       and not policy.polpermissive
       and pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) not in ('true', '(true)')),
    3, 'a rejected Storage fence alteration is rolled back by the test subtransaction'
);

-- A later permissive ALL policy cannot make source-snapshot bytes readable to
-- anon, and the inventory guard rejects that policy drift.
grant select on storage.objects to anon;
create policy snapshot_verifier_test_broad_objects_all
    on storage.objects
    as permissive
    for all
    to public
    using (true)
    with check (true);
grant select on table pg_temp.snapshot_bucket_fixture to snapshot_publisher_owner;
insert into storage.objects (bucket_id, name)
select id, 'snapshot-verifier-external-bucket-test-object'
from snapshot_bucket_fixture;
set local role anon;
select is(
    (select count(*)::integer from storage.objects where bucket_id = 'source-snapshots'),
    0,
    'a later PUBLIC ALL policy cannot expose source-snapshot objects to anon'
);
reset role;
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the Storage policy inventory rejects an unknown permissive ALL policy'
);
-- The runner holds only the PostgreSQL auto-grant (ADMIN, no SET) on the
-- publisher owner, so a temporary SET membership is added for this assertion.
-- pgtap helpers are not executable by that NOLOGIN role, so the count is
-- captured into a scratch table and asserted after RESET ROLE.
-- The scratch table lives in `public`, where the project's ensure_rls event
-- trigger enables RLS on every CREATE TABLE. It carries no policies, so the
-- INSERT would be denied; RLS is therefore switched off explicitly: this is a
-- test-local capture buffer, not a product relation.
grant snapshot_publisher_owner to postgres with set true, inherit false, admin false;
create table snapshot_owner_visibility_probe (visible integer);
alter table snapshot_owner_visibility_probe disable row level security;
grant insert, select on snapshot_owner_visibility_probe to snapshot_publisher_owner;
set local role snapshot_publisher_owner;
insert into snapshot_owner_visibility_probe
select count(*)::integer
from storage.objects
where bucket_id is distinct from 'source-snapshots';
reset role;
revoke snapshot_publisher_owner from postgres;
select is(
    (select visible from snapshot_owner_visibility_probe),
    0,
    'a later PUBLIC ALL policy cannot give the publisher owner reads from other buckets'
);
drop table snapshot_owner_visibility_probe;
drop policy snapshot_verifier_test_broad_objects_all on storage.objects;

create policy snapshot_verifier_test_unknown_restrictive_objects
    on storage.objects
    as restrictive
    for select
    to authenticated
    using (true);
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the Storage inventory rejects an unknown RESTRICTIVE policy'
);
select is(
    (select count(*)::integer from pg_catalog.pg_policy as policy
     where policy.polrelid = 'storage.objects'::pg_catalog.regclass
       and policy.polname = 'snapshot_verifier_test_unknown_restrictive_objects'
       and policy.polcmd = 'r'
       and not policy.polpermissive
       and policy.polroles = array['authenticated'::pg_catalog.regrole::oid]
       and pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) in ('true', '(true)')),
    1, 'the rejected unknown Storage policy remains unchanged for operator review'
);
drop policy snapshot_verifier_test_unknown_restrictive_objects on storage.objects;

grant select on storage.objects to anon;
create policy snapshot_verifier_test_broad_objects_select
    on storage.objects
    as permissive
    for select
    to public
    using (true);
set local role anon;
select is(
    (select count(*)::integer from storage.objects where bucket_id = 'source-snapshots'),
    0,
    'a later PUBLIC SELECT policy cannot expose source-snapshot objects to anon'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select is(
    (select count(*)::integer from storage.objects where bucket_id = 'source-snapshots'),
    0,
    'an authenticated workspace member without a source grant cannot read snapshot objects under a broad permissive policy'
);
reset role;
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the Storage policy inventory rejects an unknown permissive SELECT policy'
);

select * from finish();
rollback;
