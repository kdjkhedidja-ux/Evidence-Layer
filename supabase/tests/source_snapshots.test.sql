begin;

create extension if not exists pgtap with schema extensions;

select plan(86);

-- Every policy on public.source_snapshots is pinned by NAME, target relation,
-- command, permissive/restrictive kind and roles. A bare count would stay green
-- if one policy were renamed or swapped for another of the same shape, so each
-- policy is asserted individually. pg_temp.policy_signature renders
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
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_insert_active_owners'),
    'a:permissive:authenticated',
    'catalog policy source_snapshots_insert_active_owners is an authenticated-only INSERT policy'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_select_granted_published_or_own_staged'),
    'r:permissive:authenticated',
    'catalog policy source_snapshots_select_granted_published_or_own_staged is an authenticated-only SELECT policy'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_select_publisher_owner'),
    'r:permissive:snapshot_publisher_owner',
    'catalog policy source_snapshots_select_publisher_owner is a publisher-owner-only SELECT policy'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_update_publisher_owner'),
    'w:permissive:snapshot_publisher_owner',
    'catalog policy source_snapshots_update_publisher_owner is a publisher-owner-only UPDATE policy'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_select_verifier_role_gate_restrictive_fence'),
    'r:restrictive:PUBLIC',
    'catalog policy source_snapshots_select_verifier_role_gate_restrictive_fence is a PUBLIC restrictive role gate'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_select_authenticated_restrictive_fence'),
    'r:restrictive:authenticated',
    'catalog policy source_snapshots_select_authenticated_restrictive_fence is an authenticated restrictive fence'
);
select is(
    pg_temp.policy_signature('public.source_snapshots', 'source_snapshots_select_publisher_owner_restrictive_fence'),
    'r:restrictive:snapshot_publisher_owner',
    'catalog policy source_snapshots_select_publisher_owner_restrictive_fence is a publisher-owner restrictive fence'
);

-- Structure -----------------------------------------------------------------
select ok(
    (select relrowsecurity and relforcerowsecurity from pg_class
     where oid = 'public.source_snapshots'::regclass),
    'RLS is enabled and forced on source_snapshots'
);
select ok(
    (select relrowsecurity and relforcerowsecurity from pg_class
     where oid = 'public.source_grants'::regclass),
    'RLS is enabled and forced on source_grants'
);
select ok(
    not has_table_privilege('anon', 'public.source_snapshots', 'SELECT')
    and not has_table_privilege('anon', 'public.source_grants', 'SELECT'),
    'anon has no catalog or grant access'
);
select ok(
    not has_table_privilege('service_role', 'public.source_snapshots', 'SELECT')
    and not has_table_privilege('service_role', 'public.source_snapshots', 'INSERT')
    and not has_table_privilege('service_role', 'public.source_grants', 'SELECT')
    and not has_table_privilege('service_role', 'public.source_grants', 'INSERT'),
    'service_role holds no catalog or grant privileges'
);
select ok(
    not has_table_privilege('authenticated', 'public.source_snapshots', 'UPDATE')
    and not has_table_privilege('authenticated', 'public.source_snapshots', 'DELETE')
    and not has_column_privilege('authenticated', 'public.source_snapshots', 'state', 'UPDATE')
    and not has_column_privilege('authenticated', 'public.source_snapshots', 'published_at', 'INSERT')
    and not has_column_privilege('authenticated', 'public.source_snapshots', 'verified_sha256', 'INSERT')
    and not has_column_privilege('authenticated', 'public.source_snapshots', 'verified_size_bytes', 'INSERT')
    and not has_column_privilege('authenticated', 'public.source_snapshots', 'verified_at', 'INSERT'),
    'clients can only stage: no UPDATE/DELETE and no publication or verification columns'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'source_snapshots'
       and cmd in ('UPDATE', 'DELETE', 'ALL') and 'authenticated' = any(roles)),
    0, 'authenticated has no UPDATE, DELETE or ALL policy on source_snapshots'
);
select ok(
    not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'state', 'UPDATE')
    and not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'verified_sha256', 'UPDATE')
    and not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'verified_size_bytes', 'UPDATE')
    and not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'object_key', 'UPDATE')
    and not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'sha256', 'UPDATE')
    and not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'size_bytes', 'UPDATE')
    and not has_table_privilege('snapshot_verifier', 'public.source_snapshots', 'INSERT')
    and not has_table_privilege('snapshot_verifier', 'public.source_snapshots', 'DELETE')
    and not has_table_privilege('snapshot_verifier', 'public.source_grants', 'SELECT'),
    'the legacy snapshot_verifier role can no longer publish, alter claims, insert, delete, or read grants (publication goes through the scoped RPCs)'
);
select ok(
    not (select rolcanlogin or rolinherit or rolsuper or rolbypassrls
                or rolcreaterole or rolcreatedb or rolreplication
         from pg_roles where rolname = 'snapshot_verifier'),
    'snapshot_verifier cannot log in, inherit privileges, bypass RLS, or hold elevated attributes'
);
select ok(
    not exists (
        select 1
        from unnest(array['anon', 'authenticated', 'service_role', 'authenticator', 'supabase_storage_admin']) as api(name)
        where exists (select 1 from pg_roles where rolname = api.name)
          and pg_has_role(api.name, 'snapshot_verifier', 'member')
    ),
    'publication is closed by default: no API role can assume snapshot_verifier'
);
select ok(
    not has_table_privilege('authenticated', 'public.source_grants', 'UPDATE')
    and has_table_privilege('authenticated', 'public.source_grants', 'DELETE'),
    'source_grants has no UPDATE grant'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'source_snapshots' and cmd = 'DELETE'),
    0, 'source_snapshots has no DELETE policy'
);

-- Fixtures ------------------------------------------------------------------
insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'owner-a@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'granted@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'ungranted@example.test'),
    ('40000000-0000-4000-8000-000000000004', 'authenticated', 'authenticated', 'inactive@example.test'),
    ('50000000-0000-4000-8000-000000000005', 'authenticated', 'authenticated', 'owner-b@example.test'),
    ('60000000-0000-4000-8000-000000000006', 'authenticated', 'authenticated', 'owner-a2@example.test'),
    ('70000000-0000-4000-8000-000000000007', 'authenticated', 'authenticated', 'verifier-a@example.test');

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
    ('b3000000-0000-4000-8000-000000000003', '50000000-0000-4000-8000-000000000005', 'owner', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '60000000-0000-4000-8000-000000000006', 'owner', 'active');

-- A verifier identity scoped to workspace A only (the allowlist starts empty;
-- this operator step exists only inside the rollback-only test transaction).
-- RLS is FORCED on the allowlist, so the seeding session acts as the allowlist
-- owner and temporarily drops FORCE, then restores it immediately so every
-- later assertion still observes the production posture.
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
alter table private.snapshot_verifier_allowlist no force row level security;
insert into private.snapshot_verifier_allowlist (user_id, workspace_id)
values ('70000000-0000-4000-8000-000000000007', 'a1000000-0000-4000-8000-000000000001');
alter table private.snapshot_verifier_allowlist force row level security;
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;

-- Stored objects as Storage would record them (owner_id = uploading user).
-- d1/d2 are valid; d4 has the wrong stored size; d5 was uploaded by another
-- user; d3 has no stored object at all.
insert into storage.objects (bucket_id, name, owner_id, metadata, version)
select 'source-snapshots',
       'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/' || v.id,
       v.uploader,
       jsonb_build_object('size', v.stored_size),
       'ver-' || v.id
from (values
    ('d1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 100),
    ('d2000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', 100),
    ('d4000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000001', 999),
    ('d5000000-0000-4000-8000-000000000005', '60000000-0000-4000-8000-000000000006', 100)
) as v(id, uploader, stored_size);

create temporary table obs (name text primary key, value text not null);
grant insert, select on obs to authenticated, snapshot_verifier;

-- DML helper: records rows affected, or the SQLSTATE when it fails.
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

-- Count helper: records how many rows a query returns to the current role.
create function pg_temp.cnt(name text, q text) returns void
language plpgsql as $$
declare n integer;
begin
    execute 'select count(*) from (' || q || ') as sub' into n;
    insert into pg_temp.obs values (name, n::text);
end $$;

-- Stage helper (text SQL) so each case stays one line.
create function pg_temp.stage_sql(version text, digest text, size bigint, state text default 'staged')
returns text language sql as $$
    select format(
        $f$insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state)
           values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', %L,
                   'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/' || %L,
                   %L, %s, %L)$f$,
        version, version, digest, size, state)
$$;

-- Publication helper (text SQL): the scoped RPC, presenting the Storage object
-- identity currently recorded for the version. A missing object yields random
-- evidence so the database, not this helper, rejects it.
create function pg_temp.publish_sql(version text, digest text, size bigint)
returns text language sql as $$
    select format(
        $f$select public.publish_verified_snapshot(
               'a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
               %1$L, %2$L, %3$s,
               coalesce((select id from storage.objects where bucket_id = 'source-snapshots'
                   and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/' || %1$L), gen_random_uuid()),
               coalesce((select version from storage.objects where bucket_id = 'source-snapshots'
                   and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/' || %1$L), 'missing'))$f$,
        version, digest, size)
$$;

-- Over-grant DML only inside this rollback-only transaction so denial is
-- attributed to RLS/triggers rather than to the narrower production grants.
grant update, delete on public.source_snapshots to authenticated;
grant update on public.source_grants to authenticated;

-- Owner of workspace A: can stage, can never publish ------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);

select pg_temp.try('client_set_role_verifier', 'set local role snapshot_verifier');
select pg_temp.try('owner_stage_d1', pg_temp.stage_sql('d1000000-0000-4000-8000-000000000001', repeat('a', 64), 100));
select pg_temp.try('owner_stage_d2', pg_temp.stage_sql('d2000000-0000-4000-8000-000000000002', repeat('b', 64), 100));
select pg_temp.try('owner_stage_d3', pg_temp.stage_sql('d3000000-0000-4000-8000-000000000003', repeat('c', 64), 100));
select pg_temp.try('owner_stage_d4', pg_temp.stage_sql('d4000000-0000-4000-8000-000000000004', repeat('d', 64), 100));
select pg_temp.try('owner_stage_d5', pg_temp.stage_sql('d5000000-0000-4000-8000-000000000005', repeat('e', 64), 100));
select pg_temp.try('duplicate_version', pg_temp.stage_sql('d1000000-0000-4000-8000-000000000001', repeat('f', 64), 5));
select pg_temp.try('bad_sha', pg_temp.stage_sql('d6000000-0000-4000-8000-000000000006', 'NOT-A-DIGEST', 1));
select pg_temp.try('oversize', pg_temp.stage_sql('d7000000-0000-4000-8000-000000000007', repeat('a', 64), 8388609));
select pg_temp.try('wrong_object_key', $q$
    insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            'd8000000-0000-4000-8000-000000000008', 'workspace/other/source/x/version/y',
            repeat('a', 64), 1, 'staged')
$q$);
select pg_temp.try('insert_other_workspace', $q$
    insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state)
    values ('b3000000-0000-4000-8000-000000000003', 'c1000000-0000-4000-8000-000000000001',
            'd9000000-0000-4000-8000-000000000009',
            'workspace/b3000000-0000-4000-8000-000000000003/source/c1000000-0000-4000-8000-000000000001/version/d9000000-0000-4000-8000-000000000009',
            repeat('a', 64), 1, 'staged')
$q$);
select pg_temp.try('forged_created_by', $q$
    insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            'da000000-0000-4000-8000-00000000000a',
            'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/da000000-0000-4000-8000-00000000000a',
            repeat('a', 64), 1, 'staged', '20000000-0000-4000-8000-000000000002')
$q$);

-- No direct publication, no forged publication or verification columns.
select pg_temp.try('owner_insert_published', pg_temp.stage_sql('db000000-0000-4000-8000-00000000000b', repeat('a', 64), 100, 'published'));
select pg_temp.try('forged_published_at', $q$
    insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, published_at)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            'dc000000-0000-4000-8000-00000000000c',
            'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/dc000000-0000-4000-8000-00000000000c',
            repeat('a', 64), 1, 'staged', now())
$q$);
select pg_temp.try('forged_verified_columns', $q$
    insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, verified_sha256, verified_size_bytes)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            'dd000000-0000-4000-8000-00000000000d',
            'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/dd000000-0000-4000-8000-00000000000d',
            repeat('a', 64), 1, 'staged', repeat('a', 64), 1)
$q$);
select pg_temp.try('owner_publish_update', $q$
    update public.source_snapshots
    set state = 'published', verified_sha256 = sha256, verified_size_bytes = size_bytes
    where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);
select pg_temp.try('owner_delete_staged', $q$
    delete from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);
select pg_temp.cnt('owner_sees_own_staged', $q$ select 1 from public.source_snapshots where state = 'staged' $q$);

-- Grants: owner manages, target must be a workspace member.
select pg_temp.try('grant_member', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '20000000-0000-4000-8000-000000000002', 'read')
$q$);
select pg_temp.try('grant_non_member', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '50000000-0000-4000-8000-000000000005', 'read')
$q$);
select pg_temp.try('grant_bad_type', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '30000000-0000-4000-8000-000000000003', 'write')
$q$);
select pg_temp.try('grant_forged_granter', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type, granted_by)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '30000000-0000-4000-8000-000000000003', 'read', '20000000-0000-4000-8000-000000000002')
$q$);
select pg_temp.try('grant_duplicate', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '20000000-0000-4000-8000-000000000002', 'read')
$q$);
select pg_temp.try('grant_update', $q$
    update public.source_grants set grant_type = 'read'
    where principal_id = '20000000-0000-4000-8000-000000000002'
$q$);

-- Trusted verifier: the only path to publication is the scoped RPC ------------
reset role;
-- An Auth user scoped to workspace A through the (operator-managed) allowlist.
-- It is an ordinary `authenticated` session: it holds no table privilege that
-- lets it alter the catalog, so every statement below is judged by RLS, the
-- triggers and the RPC checks, not by a special role.
set local role authenticated;
select set_config('request.jwt.claim.sub', '70000000-0000-4000-8000-000000000007', true);
select set_config('request.jwt.claims',
    '{"sub":"70000000-0000-4000-8000-000000000007","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('verifier_sees_staged_catalog', $q$
    select 1 from public.source_snapshots
$q$);
select pg_temp.try('verifier_no_attestation', pg_temp.publish_sql('d2000000-0000-4000-8000-000000000002', null, 100));
select pg_temp.try('verifier_wrong_digest', pg_temp.publish_sql('d2000000-0000-4000-8000-000000000002', repeat('9', 64), 100));
select pg_temp.try('verifier_wrong_size', pg_temp.publish_sql('d2000000-0000-4000-8000-000000000002', repeat('b', 64), 101));
select pg_temp.try('verifier_no_stored_object', pg_temp.publish_sql('d3000000-0000-4000-8000-000000000003', repeat('c', 64), 100));
select pg_temp.try('verifier_stored_size_mismatch', pg_temp.publish_sql('d4000000-0000-4000-8000-000000000004', repeat('d', 64), 100));
select pg_temp.try('verifier_other_uploader', pg_temp.publish_sql('d5000000-0000-4000-8000-000000000005', repeat('e', 64), 100));
select pg_temp.try('verifier_alter_claim', $q$
    update public.source_snapshots set sha256 = repeat('0', 64)
    where version_id = 'd2000000-0000-4000-8000-000000000002'
$q$);
select pg_temp.try('verifier_insert', pg_temp.stage_sql('de000000-0000-4000-8000-00000000000e', repeat('a', 64), 1));
select pg_temp.try('verifier_delete', $q$
    delete from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);
select pg_temp.try('verifier_read_grants', $q$ select * from public.source_grants $q$);
select pg_temp.try('verifier_publish_d1', pg_temp.publish_sql('d1000000-0000-4000-8000-000000000001', repeat('a', 64), 100));
select pg_temp.try('verifier_publish_d2', pg_temp.publish_sql('d2000000-0000-4000-8000-000000000002', repeat('b', 64), 100));
select pg_temp.try('verifier_republish', $q$
    update public.source_snapshots set verified_sha256 = repeat('b', 64)
    where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);
reset role;

select pg_temp.cnt('published_rows_fully_stamped', $q$
    select 1 from public.source_snapshots
    where workspace_id = 'a1000000-0000-4000-8000-000000000001'
      and state = 'published' and published_at is not null and verified_at is not null
      and verified_sha256 = sha256 and verified_size_bytes = size_bytes
$q$);

-- Owner after publication: ownership implies no source access -----------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('owner_sees_published_without_grant', $q$
    select 1 from public.source_snapshots where state = 'published'
$q$);
select pg_temp.try('owner_delete_published', $q$
    delete from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);
select pg_temp.try('owner_update_published', $q$
    update public.source_snapshots set sha256 = repeat('e', 64)
    where version_id = 'd1000000-0000-4000-8000-000000000001'
$q$);

-- Member with a grant ---------------------------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('granted_sees_published', $q$ select 1 from public.source_snapshots where state = 'published' $q$);
select pg_temp.cnt('granted_sees_staged', $q$ select 1 from public.source_snapshots where state = 'staged' $q$);
select pg_temp.cnt('granted_sees_own_grant', $q$ select 1 from public.source_grants $q$);
select pg_temp.try('member_insert_snapshot', pg_temp.stage_sql('df000000-0000-4000-8000-00000000000f', repeat('a', 64), 1));

-- Member without a grant ------------------------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('ungranted_sees_grants', $q$ select 1 from public.source_grants $q$);
select pg_temp.cnt('ungranted_sees_snapshots', $q$ select 1 from public.source_snapshots $q$);
select pg_temp.try('ungranted_self_grant', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '30000000-0000-4000-8000-000000000003', 'read')
$q$);
select pg_temp.try('ungranted_delete_grant', $q$
    delete from public.source_grants where principal_id = '20000000-0000-4000-8000-000000000002'
$q$);

-- Inactive member -------------------------------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '40000000-0000-4000-8000-000000000004', true);
select set_config('request.jwt.claims',
    '{"sub":"40000000-0000-4000-8000-000000000004","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('inactive_sees_snapshots', $q$ select 1 from public.source_snapshots $q$);
select pg_temp.cnt('inactive_sees_grants', $q$ select 1 from public.source_grants $q$);

-- Owner of another workspace --------------------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-4000-8000-000000000005', true);
select set_config('request.jwt.claims',
    '{"sub":"50000000-0000-4000-8000-000000000005","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('other_owner_sees_snapshots', $q$ select 1 from public.source_snapshots $q$);
select pg_temp.try('other_owner_grants_in_a', $q$
    insert into public.source_grants (workspace_id, source_id, principal_id, grant_type)
    values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
            '30000000-0000-4000-8000-000000000003', 'read')
$q$);
select pg_temp.try('other_owner_deletes_grant', $q$
    delete from public.source_grants where principal_id = '20000000-0000-4000-8000-000000000002'
$q$);

-- A second owner of workspace A, who did not create the staged rows -----------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims',
    '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('second_owner_sees_staged', $q$ select 1 from public.source_snapshots where state = 'staged' $q$);
select pg_temp.try('second_owner_publishes_staged', $q$
    update public.source_snapshots
    set state = 'published', verified_sha256 = sha256, verified_size_bytes = size_bytes
    where version_id = 'd3000000-0000-4000-8000-000000000003'
$q$);

-- Owner revokes a grant -------------------------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('owner_revokes_grant', $q$
    delete from public.source_grants where principal_id = '20000000-0000-4000-8000-000000000002'
$q$);

-- After revocation the former grantee sees no catalog metadata either.
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('revoked_sees_snapshots', $q$ select 1 from public.source_snapshots $q$);
reset role;

-- Assertions on observations ----------------------------------------------------
select is((select value from obs where name = 'client_set_role_verifier'), '42501', 'a client session cannot assume the verifier role');
select is((select value from obs where name = 'owner_stage_d1'), '1', 'owner can stage a version');
select is((select value from obs where name = 'duplicate_version'), '23505', 'the same (workspace, source, version) cannot be cataloged twice');
select is((select value from obs where name = 'wrong_object_key'), '23514', 'object_key must be derived from the identifiers');
select is((select value from obs where name = 'bad_sha'), '23514', 'malformed digest is rejected');
select is((select value from obs where name = 'oversize'), '23514', 'size above the limit is rejected');
select is((select value from obs where name = 'insert_other_workspace'), '42501', 'owner cannot catalog into another workspace');
select is((select value from obs where name = 'forged_created_by'), '42501', 'created_by cannot be forged');
select is((select value from obs where name = 'owner_insert_published'), '42501', 'a client cannot insert a version directly as published');
select is((select value from obs where name = 'forged_published_at'), '42501', 'a client cannot supply published_at');
select is((select value from obs where name = 'forged_verified_columns'), '42501', 'a client cannot supply verification columns');
select is((select value from obs where name = 'owner_publish_update'), '0', 'an owner cannot publish a staged row, even with UPDATE over-granted');
select is((select value from obs where name = 'owner_delete_staged'), '0', 'an owner cannot delete a staged row');
select is((select value from obs where name = 'owner_sees_own_staged'), '5', 'the creator sees their own staged rows');
select is((select value from obs where name = 'verifier_sees_staged_catalog'), '0', 'the verifier identity has no direct catalog visibility of staged rows (it uses the scoped RPC)');
select is((select value from obs where name = 'verifier_no_attestation'), '22023', 'publication without evidence is rejected as invalid');
select is((select value from obs where name = 'verifier_wrong_digest'), '42501', 'a digest that differs from the staged claim is rejected');
select is((select value from obs where name = 'verifier_wrong_size'), '42501', 'a size that differs from the staged claim is rejected');
select is((select value from obs where name = 'verifier_no_stored_object'), '42501', 'publication without a stored object is rejected');
select is((select value from obs where name = 'verifier_stored_size_mismatch'), '42501', 'a stored object of the wrong size cannot be published');
select is((select value from obs where name = 'verifier_other_uploader'), '42501', 'an object uploaded by someone else cannot be published');
select is((select value from obs where name = 'verifier_alter_claim'), '0', 'the verifier identity cannot change the staged claim (no UPDATE policy, even with UPDATE over-granted)');
select is((select value from obs where name = 'verifier_insert'), '42501', 'the verifier identity cannot insert');
select is((select value from obs where name = 'verifier_delete'), '0', 'the verifier identity cannot delete (no DELETE policy, even with DELETE over-granted)');
select is((select value from obs where name = 'verifier_read_grants'), '0', 'the verifier identity sees no source grants');
select is((select value from obs where name = 'verifier_publish_d1'), '1', 'the verifier publishes a verified staged version');
select is((select value from obs where name = 'verifier_publish_d2'), '1', 'the verifier publishes a second verified version');
select is((select value from obs where name = 'verifier_republish'), '0', 'the verifier cannot modify a published row');
select is((select value from obs where name = 'published_rows_fully_stamped'), '2', 'published rows carry publication and verification evidence');
select is((select value from obs where name = 'owner_sees_published_without_grant'), '0', 'ownership alone does not grant catalog access');
select is((select value from obs where name = 'owner_delete_published'), '0', 'an owner cannot delete a published row');
select is((select value from obs where name = 'owner_update_published'), '0', 'an owner cannot alter a published row');
select is((select value from obs where name = 'granted_sees_published'), '2', 'a granted member sees published versions');
select is((select value from obs where name = 'granted_sees_staged'), '0', 'other members never see staged rows');
select is((select value from obs where name = 'member_insert_snapshot'), '42501', 'non-owner members cannot catalog versions');
select is((select value from obs where name = 'grant_member'), '1', 'owner can grant a member read access');
select is((select value from obs where name = 'grant_non_member'), '23503', 'grants require workspace membership');
select is((select value from obs where name = 'grant_bad_type'), '23514', 'only the read grant type exists');
select is((select value from obs where name = 'grant_forged_granter'), '42501', 'granted_by cannot be forged');
select is((select value from obs where name = 'grant_duplicate'), '23505', 'a grant is unique per source, principal and type');
select is((select value from obs where name = 'grant_update'), '0', 'grants cannot be updated');
select is((select value from obs where name = 'granted_sees_own_grant'), '1', 'a principal sees only their own grant');
select is((select value from obs where name = 'ungranted_sees_grants'), '0', 'a member without a grant sees no grants');
select is((select value from obs where name = 'ungranted_sees_snapshots'), '0', 'a member without a grant sees no catalog metadata');
select is((select value from obs where name = 'ungranted_self_grant'), '42501', 'members cannot grant themselves access');
select is((select value from obs where name = 'ungranted_delete_grant'), '0', 'non-owners cannot revoke grants');
select is((select value from obs where name = 'inactive_sees_snapshots'), '0', 'inactive members see no snapshots');
select is((select value from obs where name = 'inactive_sees_grants'), '0', 'inactive members see no grants');
select is((select value from obs where name = 'other_owner_sees_snapshots'), '0', 'another workspace owner sees no snapshots');
select is((select value from obs where name = 'other_owner_grants_in_a'), '42501', 'another workspace owner cannot grant in this workspace');
select is((select value from obs where name = 'other_owner_deletes_grant'), '0', 'another workspace owner cannot revoke grants');
select is((select value from obs where name = 'second_owner_sees_staged'), '0', 'another owner cannot see staged rows they did not create');
select is((select value from obs where name = 'second_owner_publishes_staged'), '0', 'another owner cannot publish a staged row they did not create');
select is((select value from obs where name = 'owner_revokes_grant'), '1', 'owner can revoke a grant');
select is((select value from obs where name = 'revoked_sees_snapshots'), '0', 'revoking a grant removes catalog visibility');

-- The triggers bind even the table owner (no RLS, no grants involved).
select throws_ok(
    $q$ update public.source_snapshots set sha256 = repeat('0', 64)
        where version_id = 'd1000000-0000-4000-8000-000000000001' $q$,
    '42501', 'source snapshot versions are immutable once cataloged',
    'a published row cannot be altered even by the table owner'
);
select throws_ok(
    $q$ delete from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001' $q$,
    '42501', 'source snapshot catalog rows cannot be deleted',
    'catalog rows cannot be deleted even by the table owner'
);
select throws_ok(
    $q$ insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, published_at, verified_at, verified_sha256, verified_size_bytes)
        values ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001',
                'e1000000-0000-4000-8000-000000000001',
                'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/e1000000-0000-4000-8000-000000000001',
                repeat('a', 64), 1, 'published', now(), now(), repeat('a', 64), 1) $q$,
    '42501', 'source snapshots must be staged before verification',
    'even the table owner cannot insert a pre-published version'
);
select throws_ok(
    $q$ update public.source_snapshots
        set state = 'published', verified_sha256 = repeat('9', 64), verified_size_bytes = 100
        where version_id = 'd3000000-0000-4000-8000-000000000003' $q$,
    '42501', 'publication requires a matching verified digest and size',
    'even the table owner cannot publish with a mismatched attestation'
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

-- A fence with the expected name and role is still unsafe if its USING clause
-- is widened. The guard must reject the altered definition; throws_ok's
-- subtransaction also proves the attempted alteration does not persist.
select throws_ok(
    $q$ do $test$
    begin
        alter policy source_snapshots_select_verifier_role_gate_restrictive_fence
            on public.source_snapshots using (true);
        alter policy source_snapshots_select_authenticated_restrictive_fence
            on public.source_snapshots using (true);
        alter policy source_snapshots_select_publisher_owner_restrictive_fence
            on public.source_snapshots using (true);
        execute 'set role snapshot_verifier_allowlist_owner';
        perform private.assert_snapshot_read_policy_inventory_v2(true);
        raise exception using errcode = 'P0001',
            message = 'test sentinel: guard accepted a widened catalog fence';
    end;
    $test$ $q$,
    '55000', 'snapshot read restrictive fence definition changed',
    'the guard rejects known catalog RESTRICTIVE policies widened to USING (true)'
);
select is(
    (select count(*)::integer from pg_catalog.pg_policy as policy
     where policy.polrelid = 'public.source_snapshots'::pg_catalog.regclass
       and policy.polname in (
           'source_snapshots_select_verifier_role_gate_restrictive_fence',
           'source_snapshots_select_authenticated_restrictive_fence',
           'source_snapshots_select_publisher_owner_restrictive_fence')
       and not policy.polpermissive
       and pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) not in ('true', '(true)')),
    3, 'a rejected catalog fence alteration is rolled back by the test subtransaction'
);

-- A later permissive SELECT policy cannot widen catalog reads past the
-- permanent restrictive fence. The inventory guard also rejects the drift.
grant select on public.source_snapshots to anon;
create policy snapshot_verifier_test_broad_catalog_select
    on public.source_snapshots
    as permissive
    for select
    to public
    using (true);
set local role anon;
select is(
    (select count(*)::integer from public.source_snapshots),
    0,
    'a later PUBLIC SELECT policy cannot expose snapshot catalog rows to anon'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select is(
    (select count(*)::integer from public.source_snapshots),
    0,
    'an authenticated workspace member without a source grant cannot read catalog rows under a broad permissive policy'
);
reset role;
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the catalog policy inventory rejects an unknown permissive SELECT policy'
);
drop policy snapshot_verifier_test_broad_catalog_select on public.source_snapshots;

create policy snapshot_verifier_test_unknown_restrictive_catalog
    on public.source_snapshots
    as restrictive
    for select
    to authenticated
    using (true);
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the catalog inventory rejects an unknown RESTRICTIVE policy'
);
select is(
    (select count(*)::integer from pg_catalog.pg_policy as policy
     where policy.polrelid = 'public.source_snapshots'::pg_catalog.regclass
       and policy.polname = 'snapshot_verifier_test_unknown_restrictive_catalog'
       and policy.polcmd = 'r'
       and not policy.polpermissive
       and policy.polroles = array['authenticated'::pg_catalog.regrole::oid]
       and pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) in ('true', '(true)')),
    1, 'the rejected unknown catalog policy remains unchanged for operator review'
);
drop policy snapshot_verifier_test_unknown_restrictive_catalog on public.source_snapshots;

grant select on public.source_snapshots to anon;
create policy snapshot_verifier_test_broad_catalog_all
    on public.source_snapshots
    as permissive
    for all
    to public
    using (true)
    with check (true);
set local role anon;
select is(
    (select count(*)::integer from public.source_snapshots),
    0,
    'a later PUBLIC ALL policy cannot expose snapshot catalog rows to anon'
);
reset role;
select throws_ok(
    $q$ select pg_temp.inventory_drift() $q$,
    '55000', 'unapproved SELECT or ALL policy exists on a snapshot read relation',
    'the catalog policy inventory rejects an unknown permissive ALL policy'
);

select * from finish();
rollback;
