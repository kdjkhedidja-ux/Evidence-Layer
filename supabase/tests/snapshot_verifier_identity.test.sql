begin;

create extension if not exists pgtap with schema extensions;

select plan(119);

-- Verifier identity (20261022120000). Prepared, not yet executed against a database.

-- Structure -----------------------------------------------------------------
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'private.snapshot_verifier_allowlist'::regclass), 'the verifier allowlist has RLS enabled and forced');
select is((select count(*)::integer from private.snapshot_verifier_allowlist), 0, 'the verifier allowlist is empty after migration (publication closed by default)');
select is((select count(*)::integer from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in ('begin_snapshot_verification', 'publish_verified_snapshot', 'verifier_may_read_staged_object')
      and p.prosecdef and p.proowner = 'snapshot_publisher_owner'::regrole
      and p.proconfig @> array['search_path=""']), 3,
    'all verifier functions are security definer, owned by the publisher role, with an empty search_path');
select ok((select not rolcanlogin and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb from pg_roles where rolname = 'snapshot_publisher_owner'), 'the function owner cannot log in and holds no elevated attribute');
select ok((select count(*) = 2 and bool_and(not rolcanlogin and not rolinherit and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb and not rolreplication) from pg_roles where rolname in ('snapshot_publisher_owner', 'snapshot_verifier')), 'both verifier roles are non-login, non-inheriting and hold no elevated attributes');
select ok(not exists (
    select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role', 'authenticator')
      and (pg_has_role(r.rolname, 'snapshot_publisher_owner', 'member') or pg_has_role(r.rolname, 'snapshot_verifier', 'member'))),
    'no API role can assume the publisher owner or the legacy verifier role');
select ok(not exists (
    select 1 from unnest(array['authenticated', 'anon', 'service_role']) as r(name)
    cross join unnest(array['select', 'insert', 'update', 'delete']) as p(priv)
    where has_table_privilege(r.name, 'private.snapshot_verifier_allowlist', p.priv)),
    'no API role holds any privilege on the allowlist table');
select ok(not exists (
    select 1 from unnest(array['anon', 'service_role']) as r(name)
    cross join unnest(array['public.begin_snapshot_verification(uuid,uuid,uuid)',
                            'public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)',
                            'public.verifier_may_read_staged_object(text)']) as f(sig)
    where has_function_privilege(r.name, f.sig, 'execute')),
    'anon and service_role cannot execute the verifier functions');
select ok((select bool_and(has_function_privilege('authenticated', f.sig, 'execute'))
    from unnest(array['public.begin_snapshot_verification(uuid,uuid,uuid)',
                      'public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)',
                      'public.verifier_may_read_staged_object(text)']) as f(sig)),
    'authenticated can execute the verifier functions (the allowlist decides inside)');
select ok(
    not has_table_privilege('snapshot_publisher_owner', 'private.snapshot_verifier_allowlist', 'insert')
    and not has_table_privilege('snapshot_publisher_owner', 'private.snapshot_verifier_allowlist', 'update')
    and not has_table_privilege('snapshot_publisher_owner', 'private.snapshot_verifier_allowlist', 'delete')
    and not has_table_privilege('snapshot_publisher_owner', 'storage.objects', 'insert')
    and not has_table_privilege('snapshot_publisher_owner', 'storage.objects', 'update')
    and not has_table_privilege('snapshot_publisher_owner', 'storage.objects', 'delete')
    and not has_table_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'insert')
    and not has_table_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'delete')
    and not has_table_privilege('snapshot_publisher_owner', 'public.source_grants', 'select')
    and not has_table_privilege('snapshot_publisher_owner', 'public.workspace_memberships', 'select'),
    'the function owner cannot change the allowlist, Storage, grants, memberships, or insert/delete catalog rows');
select ok(
    not has_column_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'sha256', 'update')
    and not has_column_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'object_key', 'update')
    and not has_column_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'size_bytes', 'update')
    and not has_column_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'workspace_id', 'update')
    and not has_column_privilege('snapshot_publisher_owner', 'public.source_snapshots', 'created_by', 'update'),
    'the function owner can update only the publication columns');
select ok(not has_column_privilege('snapshot_verifier', 'public.source_snapshots', 'state', 'update'), 'the legacy direct-UPDATE publication route is closed');
select ok(not has_table_privilege('snapshot_verifier', 'public.source_snapshots', 'select') and not has_table_privilege('snapshot_verifier', 'storage.objects', 'select'), 'the legacy verifier role has no direct catalog or Storage privileges');
select is((select count(*)::integer from pg_policies where (schemaname, tablename) in (('public', 'source_snapshots'), ('storage', 'objects')) and roles @> array['snapshot_verifier'::name]), 0, 'no direct RLS policy targets the legacy verifier role');
select is((
    select count(*)::integer
    from pg_auth_members
    where (roleid in ('snapshot_publisher_owner'::regrole, 'snapshot_verifier'::regrole)
           or member in ('snapshot_publisher_owner'::regrole, 'snapshot_verifier'::regrole))
      -- PostgreSQL 16+ automatically grants each newly created role to the
      -- non-superuser CREATEROLE migration actor (postgres) with ADMIN OPTION
      -- only. Tolerate exactly that automatic edge; every other membership is
      -- still forbidden.
      and not (
          member = 'postgres'::regrole
          and roleid in ('snapshot_publisher_owner'::regrole, 'snapshot_verifier'::regrole)
          and admin_option
          and not set_option
          and not inherit_option
      )
), 0, 'no verifier identity has a membership other than the automatic admin edge for the migration actor');
select is((select count(*)::integer from pg_policies
    where schemaname = 'storage' and tablename = 'objects'
      and policyname like 'source_snapshots_objects_%' and cmd not in ('INSERT', 'SELECT')), 0,
    'the verifier migration adds no UPDATE, DELETE or ALL Storage policy');

-- Role safety guard ---------------------------------------------------------
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_missing') $q$,
    '55000', 'snapshot verifier role does not exist',
    'an absent role is rejected instead of being silently assumed safe'
);

create role snapshot_role_guard_safe
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
select lives_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_safe') $q$,
    'an existing isolated role passes the role guard'
);

create role snapshot_role_guard_login login
    noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_login') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing login-capable role is rejected'
);

create role snapshot_role_guard_inherit_attr
    nologin inherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_inherit_attr') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing inheriting role is rejected'
);
-- Exactly one assertion is emitted regardless of runner privileges. Instead of
-- requiring a superuser runner to create a SUPERUSER fixture, the guard is
-- exercised against an existing superuser role (the bootstrap superuser of a
-- vanilla cluster, supabase_admin in the Supabase image). The attribute check
-- runs before the ownership and membership checks, so the unsafe-attribute
-- branch is the one that fires.
select throws_ok(
    pg_catalog.format(
        'select private.assert_snapshot_role_safe(%L)',
        (select role_row.rolname
         from pg_catalog.pg_roles as role_row
         where role_row.rolsuper
         order by role_row.oid
         limit 1)
    ),
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing superuser role is rejected'
);
create role snapshot_role_guard_bypassrls
    nologin noinherit bypassrls nosuperuser nocreatedb nocreaterole noreplication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_bypassrls') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing BYPASSRLS role is rejected'
);
create role snapshot_role_guard_createdb
    nologin noinherit nobypassrls nosuperuser createdb nocreaterole noreplication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_createdb') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing CREATEDB role is rejected'
);
create role snapshot_role_guard_createrole
    nologin noinherit nobypassrls nosuperuser nocreatedb createrole noreplication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_createrole') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing CREATEROLE role is rejected'
);
create role snapshot_role_guard_replication
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole replication;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_replication') $q$,
    '55000', 'snapshot verifier role has unsafe role attributes',
    'an existing REPLICATION role is rejected'
);

create role snapshot_role_guard_set_target
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
create role snapshot_role_guard_set_grantee nologin noinherit;
grant snapshot_role_guard_set_target to snapshot_role_guard_set_grantee
    with set true, inherit false;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_set_target') $q$,
    '55000', 'snapshot verifier role has an enabled SET, INHERIT, or ADMIN membership option',
    'a membership with SET enabled is rejected'
);

create role snapshot_role_guard_inherit_target
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
create role snapshot_role_guard_inherit_grantee nologin noinherit;
grant snapshot_role_guard_inherit_target to snapshot_role_guard_inherit_grantee
    with set false, inherit true;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_inherit_target') $q$,
    '55000', 'snapshot verifier role has an enabled SET, INHERIT, or ADMIN membership option',
    'a membership with INHERIT enabled is rejected'
);

create role snapshot_role_guard_edge_target
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
create role snapshot_role_guard_edge_grantee nologin noinherit;
grant snapshot_role_guard_edge_target to snapshot_role_guard_edge_grantee
    with set false, inherit false, admin false;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_edge_target') $q$,
    '55000', 'snapshot verifier role has direct or transitive role memberships',
    'a disabled membership edge is still rejected'
);

create role snapshot_role_guard_chain_target
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
create role snapshot_role_guard_chain_middle nologin noinherit;
create role snapshot_role_guard_chain_leaf nologin noinherit;
grant snapshot_role_guard_chain_target to snapshot_role_guard_chain_middle
    with set false, inherit false, admin false;
grant snapshot_role_guard_chain_middle to snapshot_role_guard_chain_leaf
    with set false, inherit false, admin false;
-- No membership option is enabled, so this path reaches the membership-graph
-- check instead of failing on SET/INHERIT/ADMIN first.
alter role snapshot_role_guard_chain_middle noinherit;
select ok(
    (
        with recursive membership_path(roleid, depth) as (
            select roleid, 1
            from pg_auth_members
            where member = 'snapshot_role_guard_chain_leaf'::regrole
            union all
            select membership.roleid, path.depth + 1
            from pg_auth_members as membership
            join membership_path as path on membership.member = path.roleid
        )
        select exists (
            select 1 from membership_path
            where roleid = 'snapshot_role_guard_chain_target'::regrole and depth >= 2
        )
    ),
    'the test fixture contains a two-edge membership path with SET, INHERIT and ADMIN disabled'
);
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_chain_target') $q$,
    '55000', 'snapshot verifier role has direct or transitive role memberships',
    'a transitive membership path is rejected'
);

-- The API membership check must follow a transitive SET path even when the API
-- role has no direct edge to snapshot_publisher_owner.
create role snapshot_api_membership_chain_bridge nologin noinherit;
grant snapshot_publisher_owner to snapshot_api_membership_chain_bridge
    with set true, inherit false, admin false;
grant snapshot_api_membership_chain_bridge to authenticated
    with set true, inherit false, admin false;
select ok(
    not exists (
        select 1 from pg_auth_members
        where (roleid = 'snapshot_publisher_owner'::regrole
               and member = 'authenticated'::regrole)
           or (roleid = 'authenticated'::regrole
               and member = 'snapshot_publisher_owner'::regrole)
    ),
    'the authenticated role has no direct membership edge to the publisher owner'
);
select ok(
    pg_has_role('authenticated', 'snapshot_publisher_owner', 'member'),
    'the test fixture grants publisher-owner SET only through an intermediate role'
);
select throws_ok(
    $q$ select private.assert_snapshot_api_role_cannot_assume(
        'authenticated', 'snapshot_publisher_owner'
    ) $q$,
    '55000', 'an API role can assume a verifier role: authenticated -> snapshot_publisher_owner',
    'the API-role guard rejects a transitive role-assumption path'
);
revoke snapshot_api_membership_chain_bridge from authenticated;
revoke snapshot_publisher_owner from snapshot_api_membership_chain_bridge;

create role snapshot_role_guard_admin_target
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
create role snapshot_role_guard_admin_grantee nologin noinherit;
grant snapshot_role_guard_admin_target to snapshot_role_guard_admin_grantee
    with set false, inherit false, admin true;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_admin_target') $q$,
    '55000', 'snapshot verifier role has an enabled SET, INHERIT, or ADMIN membership option',
    'a membership with ADMIN enabled is rejected'
);

create role snapshot_role_guard_acl nologin noinherit;
create table public.snapshot_role_guard_acl_probe (id integer primary key);
grant select on public.snapshot_role_guard_acl_probe to snapshot_role_guard_acl;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_acl') $q$,
    '55000', 'snapshot verifier role owns objects or has pre-existing object privileges',
    'pre-existing object privileges are rejected'
);

create role snapshot_role_guard_owner nologin noinherit;
grant create on schema public to snapshot_role_guard_owner;
-- SET on the target is required to transfer ownership; grant it temporarily to
-- the migration actor and remove it before the guard runs.
grant snapshot_role_guard_owner to postgres with set true, inherit false, admin false;
create table public.snapshot_role_guard_owner_probe (id integer primary key);
alter table public.snapshot_role_guard_owner_probe owner to snapshot_role_guard_owner;
revoke snapshot_role_guard_owner from postgres;
revoke create on schema public from snapshot_role_guard_owner;
select throws_ok(
    $q$ select private.assert_snapshot_role_safe('snapshot_role_guard_owner') $q$,
    '55000', 'snapshot verifier role owns objects or has pre-existing object privileges',
    'pre-existing object ownership is rejected'
);

-- The allowlist control roles and their assertion function are owned by
-- snapshot_verifier_allowlist_owner once the migration finalizes, so the pgTAP
-- runner (postgres, a CREATEROLE but non-superuser role) has no EXECUTE on the
-- internal shape assertion. Grant it for the duration of this rolled-back test
-- transaction, using a SET membership that is removed immediately so the
-- membership-graph assertions still observe only the PostgreSQL auto-grant edge.
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
grant execute on function private.assert_snapshot_verifier_allowlist_admin_shape(pg_catalog.regclass)
    to postgres;
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;

-- Function ACL/ownership guard ----------------------------------------------
-- begin_snapshot_verification is owned by snapshot_publisher_owner after the
-- ownership transfer, so the unapproved EXECUTE grant below must be issued as
-- that owner (the SET membership is temporary and removed before the guard runs).
grant snapshot_publisher_owner to postgres with set true, inherit false, admin false;
set local role snapshot_publisher_owner;
grant execute on function public.begin_snapshot_verification(uuid, uuid, uuid) to anon;
reset role;
select throws_ok(
    $q$ select private.assert_snapshot_verifier_function_acl_safe() $q$,
    '55000', 'verifier function EXECUTE grants differ from the authenticated-only allowlist',
    'an unapproved EXECUTE grant causes the migration guard to fail'
);
set local role snapshot_publisher_owner;
revoke execute on function public.begin_snapshot_verification(uuid, uuid, uuid) from anon;
reset role;
revoke snapshot_publisher_owner from postgres;
select lives_ok(
    $q$ select private.assert_snapshot_verifier_function_acl_safe() $q$,
    'the function guard passes after the unapproved test grant is removed'
);

grant create on schema public to snapshot_publisher_owner;
-- SET on the target is required to transfer ownership; grant it temporarily and
-- remove it before the function-ACL guard runs.
grant snapshot_publisher_owner to postgres with set true, inherit false, admin false;
create table public.snapshot_publisher_owner_extra_probe (id integer primary key);
alter table public.snapshot_publisher_owner_extra_probe owner to snapshot_publisher_owner;
revoke snapshot_publisher_owner from postgres;
revoke create on schema public from snapshot_publisher_owner;
select throws_ok(
    $q$ select private.assert_snapshot_verifier_function_acl_safe() $q$,
    '55000', 'snapshot_publisher_owner owns an unapproved object',
    'an extra table owned by the function owner is rejected'
);

-- The corrected allowlist validator accepts the migration's exact shape and
-- rejects incompatible fixtures before any privileges or policies are changed.
select lives_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_admin_shape(
        'private.snapshot_verifier_allowlist'::regclass
    ) $q$,
    'the current allowlist satisfies the ownership, RLS, policy, membership and ACL contract'
);
select is(
    (select count(*)::integer from pg_catalog.pg_roles
     where rolname in ('snapshot_verifier_allowlist_owner',
                       'snapshot_verifier_allowlist_manager',
                       'snapshot_verifier_audit_owner')
       and not rolcanlogin and not rolinherit and not rolsuper and not rolbypassrls
       and not rolcreatedb and not rolcreaterole and not rolreplication),
    3, 'the allowlist owner, manager and audit owner are non-login roles without elevated attributes'
);
select is(
    (select count(*)::integer from pg_catalog.pg_auth_members
     where (roleid in ('snapshot_verifier_allowlist_owner'::regrole,
                       'snapshot_verifier_allowlist_manager'::regrole,
                       'snapshot_verifier_audit_owner'::regrole)
         or member in ('snapshot_verifier_allowlist_owner'::regrole,
                       'snapshot_verifier_allowlist_manager'::regrole,
                       'snapshot_verifier_audit_owner'::regrole))
       -- The non-superuser CREATEROLE actor that ran CREATE ROLE leaves one
       -- ADMIN-only, non-SET, non-INHERIT edge per control role. That edge is
       -- the only tolerated membership; every other edge is a failure.
       and not (
           member = current_user::regrole
           and admin_option and not set_option and not inherit_option
       )),
    0, 'no role can inherit, SET, or administer a snapshot control role'
);
select ok(
    (select relowner = 'snapshot_verifier_allowlist_owner'::regrole
     from pg_catalog.pg_class
     where oid = 'private.snapshot_verifier_allowlist'::regclass)
    and not pg_catalog.has_table_privilege(
        'snapshot_publisher_owner', 'private.snapshot_verifier_allowlist', 'INSERT'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_publisher_owner', 'private.snapshot_verifier_allowlist', 'DELETE'
    )
    and not pg_catalog.has_table_privilege(
        'authenticated', 'private.snapshot_verifier_read_policy_baseline', 'SELECT'
    )
    and not pg_catalog.has_function_privilege(
        'authenticated',
        'private.assert_snapshot_read_policy_inventory_v2(boolean)', 'EXECUTE'
    )
    and not pg_catalog.has_function_privilege(
        'authenticated',
        'private.assert_snapshot_verifier_allowlist_admin_shape(regclass)', 'EXECUTE'
    ),
    'the allowlist owner is separate from the publisher function owner'
);
select ok(
    pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'DELETE'
    )
    and pg_catalog.has_column_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'user_id', 'SELECT'
    )
    and pg_catalog.has_column_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'workspace_id', 'SELECT'
    )
    and pg_catalog.has_column_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'user_id', 'INSERT'
    )
    and pg_catalog.has_column_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'workspace_id', 'INSERT'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'UPDATE'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'TRUNCATE'
    )
    and not pg_catalog.has_column_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist', 'added_at', 'SELECT'
    ),
    'the manager can add/remove scoped identities but cannot update, truncate or read added_at'
);
select throws_ok(
    $q$ do $test$
    begin
        alter role snapshot_verifier_allowlist_manager login;
        perform private.assert_snapshot_verifier_allowlist_admin_shape(
            'private.snapshot_verifier_allowlist'::regclass
        );
        raise exception using errcode = 'P0001', message = 'test sentinel';
    end;
    $test$ $q$,
    '55000', 'snapshot control role attributes or membership graph are unsafe',
    'the shape guard rejects LOGIN on the row manager'
);
select throws_ok(
    $q$ do $test$
    begin
        alter role snapshot_verifier_allowlist_manager inherit;
        perform private.assert_snapshot_verifier_allowlist_admin_shape(
            'private.snapshot_verifier_allowlist'::regclass
        );
        raise exception using errcode = 'P0001', message = 'test sentinel';
    end;
    $test$ $q$,
    '55000', 'snapshot control role attributes or membership graph are unsafe',
    'the shape guard rejects INHERIT on the row manager'
);
select throws_ok(
    $q$ do $test$
    begin
        alter role snapshot_verifier_allowlist_manager bypassrls;
        perform private.assert_snapshot_verifier_allowlist_admin_shape(
            'private.snapshot_verifier_allowlist'::regclass
        );
        raise exception using errcode = 'P0001', message = 'test sentinel';
    end;
    $test$ $q$,
    '55000', 'snapshot control role attributes or membership graph are unsafe',
    'the shape guard rejects BYPASSRLS on the row manager'
);
select throws_ok(
    $q$ do $test$
    begin
        alter role snapshot_verifier_allowlist_manager createrole;
        perform private.assert_snapshot_verifier_allowlist_admin_shape(
            'private.snapshot_verifier_allowlist'::regclass
        );
        raise exception using errcode = 'P0001', message = 'test sentinel';
    end;
    $test$ $q$,
    '55000', 'snapshot control role attributes or membership graph are unsafe',
    'the shape guard rejects CREATEROLE on the row manager'
);
create role snapshot_admin_membership_probe nologin noinherit;
select throws_ok(
    $q$ do $test$
    begin
        grant snapshot_verifier_allowlist_manager to snapshot_admin_membership_probe
            with set true, inherit false, admin false;
        perform private.assert_snapshot_verifier_allowlist_admin_shape(
            'private.snapshot_verifier_allowlist'::regclass
        );
        raise exception using errcode = 'P0001', message = 'test sentinel';
    end;
    $test$ $q$,
    '55000', 'snapshot control role attributes or membership graph are unsafe',
    'the shape guard rejects a transitive SET membership even with INHERIT and ADMIN disabled'
);
drop role snapshot_admin_membership_probe;
select ok(
    (select relowner = 'snapshot_verifier_audit_owner'::regrole
            and relrowsecurity and relforcerowsecurity
     from pg_catalog.pg_class
     where oid = 'private.snapshot_verifier_allowlist_audit'::regclass)
    and (select count(*) = 2 from pg_catalog.pg_policy
         where polrelid = 'private.snapshot_verifier_allowlist_audit'::regclass),
    'the audit log has its isolated owner, forced RLS and only the trigger-write and manager-read policies'
);
select ok(
    pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist_audit', 'SELECT'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist_audit', 'INSERT'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist_audit', 'UPDATE'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist_audit', 'DELETE'
    )
    and not pg_catalog.has_table_privilege(
        'snapshot_verifier_allowlist_manager', 'private.snapshot_verifier_allowlist_audit', 'TRUNCATE'
    ),
    'the manager may review audit entries but cannot write, delete or truncate the audit log'
);
select ok(
    (select prosecdef and proowner = 'snapshot_verifier_audit_owner'::regrole
            and proconfig @> array['search_path=""']
     from pg_catalog.pg_proc
     where oid = 'private.audit_snapshot_verifier_allowlist_change()'::regprocedure)
    and not exists (
        select 1 from pg_catalog.pg_proc as function_row
        cross join lateral pg_catalog.aclexplode(
            coalesce(function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner))
        ) as acl
        where function_row.oid = 'private.audit_snapshot_verifier_allowlist_change()'::regprocedure
          and (acl.grantee <> 'snapshot_verifier_audit_owner'::regrole
               or acl.privilege_type <> 'EXECUTE')
    ),
    'the audit trigger function is privately executable only by its owner with an empty search_path'
);

-- The allowlist table is owned by snapshot_verifier_allowlist_owner, so creating
-- and dropping the test policy requires acting as that owner. The SET membership
-- is temporary and removed before the shape guard inspects the membership graph.
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
create policy snapshot_verifier_test_unknown_allowlist_policy
    on private.snapshot_verifier_allowlist for select to authenticated using (true);
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_admin_shape(
        'private.snapshot_verifier_allowlist'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible row policies',
    'an unapproved allowlist policy is rejected'
);
select is(
    (select count(*)::integer from pg_catalog.pg_policy
     where polrelid = 'private.snapshot_verifier_allowlist'::regclass
       and polname = 'snapshot_verifier_test_unknown_allowlist_policy'
       and polcmd = 'r' and polpermissive
       and polroles = array['authenticated'::regrole::oid]
       and pg_catalog.pg_get_expr(polqual, polrelid, true) in ('true', '(true)')),
    1, 'the rejected allowlist policy remains in place unchanged for operator review'
);
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
drop policy snapshot_verifier_test_unknown_allowlist_policy on private.snapshot_verifier_allowlist;
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;
create temporary table snapshot_allowlist_missing_workspace (
    user_id uuid not null,
    added_at timestamptz not null default now(),
    primary key (user_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'pg_temp.snapshot_allowlist_missing_workspace'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible columns or defaults',
    'a missing workspace column is rejected'
);
create temporary table snapshot_allowlist_wrong_type (
    user_id uuid not null,
    workspace_id text not null,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'pg_temp.snapshot_allowlist_wrong_type'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible columns or defaults',
    'a non-UUID workspace column is rejected'
);
create temporary table snapshot_allowlist_nullable_workspace (
    user_id uuid not null,
    workspace_id uuid,
    added_at timestamptz not null default now(),
    -- The primary key must not include workspace_id, otherwise PostgreSQL would
    -- implicitly make it NOT NULL and the fixture could not be nullable.
    primary key (user_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'pg_temp.snapshot_allowlist_nullable_workspace'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible columns or defaults',
    'a nullable workspace column is rejected'
);
create temporary table snapshot_allowlist_wrong_primary_key (
    user_id uuid not null,
    workspace_id uuid not null,
    added_at timestamptz not null default now(),
    primary key (workspace_id, user_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'pg_temp.snapshot_allowlist_wrong_primary_key'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has an incompatible primary key',
    'a differently ordered primary key is rejected'
);
-- PostgreSQL forbids a temporary table from carrying a foreign key to a
-- permanent relation, so the constraint fixtures live in a scratch schema that
-- is dropped by the final ROLLBACK.
create schema snapshot_guard_scratch;
create table snapshot_guard_scratch.snapshot_allowlist_wrong_foreign_key (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references auth.users (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'snapshot_guard_scratch.snapshot_allowlist_wrong_foreign_key'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible foreign keys',
    'a foreign key to the wrong relation is rejected'
);
create table snapshot_guard_scratch.snapshot_allowlist_extra_constraint (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id),
    check (workspace_id is not null)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'snapshot_guard_scratch.snapshot_allowlist_extra_constraint'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible constraints',
    'an unexpected constraint is rejected'
);
create table snapshot_guard_scratch.snapshot_allowlist_column_acl (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);
alter table snapshot_guard_scratch.snapshot_allowlist_column_acl enable row level security;
alter table snapshot_guard_scratch.snapshot_allowlist_column_acl force row level security;
grant select on table snapshot_guard_scratch.snapshot_allowlist_column_acl to snapshot_publisher_owner;
grant insert (workspace_id) on table snapshot_guard_scratch.snapshot_allowlist_column_acl to anon;
create policy snapshot_verifier_allowlist_select_owner
    on snapshot_guard_scratch.snapshot_allowlist_column_acl
    for select to snapshot_publisher_owner using (true);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'snapshot_guard_scratch.snapshot_allowlist_column_acl'::regclass
    ) $q$,
    '42501', 'snapshot verifier allowlist is exposed to an API role or PUBLIC',
    'a column-level API grant on the allowlist is rejected'
);
create table snapshot_guard_scratch.snapshot_allowlist_rls_disabled (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'snapshot_guard_scratch.snapshot_allowlist_rls_disabled'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist must have RLS enabled and forced',
    'an allowlist table without RLS is rejected'
);
create table snapshot_guard_scratch.snapshot_allowlist_unexpected_policy (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);
alter table snapshot_guard_scratch.snapshot_allowlist_unexpected_policy enable row level security;
alter table snapshot_guard_scratch.snapshot_allowlist_unexpected_policy force row level security;
create policy snapshot_allowlist_unexpected_policy_read
    on snapshot_guard_scratch.snapshot_allowlist_unexpected_policy for select to anon using (true);
select throws_ok(
    $q$ select private.assert_snapshot_verifier_allowlist_shape(
        'snapshot_guard_scratch.snapshot_allowlist_unexpected_policy'::regclass
    ) $q$,
    '55000', 'snapshot verifier allowlist has incompatible row policies',
    'an unexpected allowlist policy is rejected'
);

-- Model a failure at the temporary-membership REVOKE boundary. The exception
-- subtransaction must roll back the membership, schema grant and owner change.
-- The temporary SET membership is granted to the migration actor (current_user),
-- exactly as the migration does; a separate actor role cannot SET ROLE to the
-- target and would fail before the boundary under test.
create role snapshot_transfer_atomic_target nologin noinherit;
create function public.snapshot_transfer_atomic_probe()
returns integer language sql immutable as 'select 1';
create temporary table snapshot_transfer_atomic_before as
select proowner as owner_oid from pg_proc
where oid = 'public.snapshot_transfer_atomic_probe()'::regprocedure;
select lives_ok(
    $q$ do $atomic$
    declare transfer_completed boolean := false;
    begin
        begin
            execute 'grant create on schema public to snapshot_transfer_atomic_target';
            execute 'grant snapshot_transfer_atomic_target to current_user '
                    || 'with set true, inherit false, admin false';
            execute 'alter function public.snapshot_transfer_atomic_probe() '
                    || 'owner to snapshot_transfer_atomic_target';
            transfer_completed := true;
            execute 'revoke snapshot_transfer_atomic_target from snapshot_transfer_missing_actor';
        exception when undefined_object then
            if not transfer_completed then
                raise;
            end if;
        end;
    end;
    $atomic$ $q$,
    'a simulated REVOKE failure is caught only after the owner transfer occurred'
);
select ok(
    (select function_row.proowner = before_transfer.owner_oid
     from pg_proc as function_row cross join snapshot_transfer_atomic_before as before_transfer
     where function_row.oid = 'public.snapshot_transfer_atomic_probe()'::regprocedure),
    'the failed temporary-membership REVOKE rolled the owner transfer back'
);
select ok(
    not exists (
        select 1 from pg_auth_members
        where roleid = 'snapshot_transfer_atomic_target'::regrole
          and not (
              -- tolerate the automatic admin edge of the CREATEROLE actor
              member = 'postgres'::regrole
              and admin_option and not set_option and not inherit_option
          )
    ),
    'the failed transfer left no temporary role membership'
);
select ok(
    not has_schema_privilege('snapshot_transfer_atomic_target', 'public', 'CREATE'),
    'the failed transfer left no temporary schema CREATE privilege'
);

-- Fixtures ------------------------------------------------------------------
insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'owner-a@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'outsider@example.test'),
    ('60000000-0000-4000-8000-000000000006', 'authenticated', 'authenticated', 'verifier@example.test'),
    ('70000000-0000-4000-8000-000000000007', 'authenticated', 'authenticated', 'verifier-b@example.test'),
    ('50000000-0000-4000-8000-000000000005', 'authenticated', 'authenticated', 'owner-b@example.test');

insert into public.workspaces (id, name) values ('a1000000-0000-4000-8000-000000000001', 'A'), ('b3000000-0000-4000-8000-000000000003', 'B');
insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'), ('b3000000-0000-4000-8000-000000000003', '50000000-0000-4000-8000-000000000005', 'owner', 'active');

insert into public.source_snapshots (workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by)
values
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, 'staged', '10000000-0000-4000-8000-000000000001'),
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd2000000-0000-4000-8000-000000000002', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d2000000-0000-4000-8000-000000000002', repeat('a', 64), 100, 'staged', '10000000-0000-4000-8000-000000000001'),
    ('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd3000000-0000-4000-8000-000000000003', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d3000000-0000-4000-8000-000000000003', repeat('a', 64), 100, 'staged', '10000000-0000-4000-8000-000000000001'),
    ('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009', 'workspace/b3000000-0000-4000-8000-000000000003/source/c2000000-0000-4000-8000-000000000002/version/d9000000-0000-4000-8000-000000000009', repeat('a', 64), 100, 'staged', '50000000-0000-4000-8000-000000000005');

-- v1 matches its claim; v2 was stored with a different size; v3 has no object; v9 belongs to B.
insert into storage.objects (bucket_id, name, owner_id, metadata, version)
values
    ('source-snapshots', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', '{"size": 100}', 'ver-1'),
    ('source-snapshots', 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d2000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', '{"size": 99}', 'ver-2'),
    ('source-snapshots', 'orphan/object-without-catalog-row', '10000000-0000-4000-8000-000000000001', '{"size": 1}', 'ver-orphan'),
    ('source-snapshots', 'workspace/b3000000-0000-4000-8000-000000000003/source/c2000000-0000-4000-8000-000000000002/version/d9000000-0000-4000-8000-000000000009', '50000000-0000-4000-8000-000000000005', '{"size": 100}', 'ver-9');

create temporary table ids as
select case name when 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001' then 'v1' when 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d2000000-0000-4000-8000-000000000002' then 'v2' else 'v9' end as label, id, version
from storage.objects where bucket_id = 'source-snapshots' and name like 'workspace/%';

-- Seeding runs as the allowlist owner. RLS is FORCED on the table, so the
-- owner must temporarily bypass it to insert the fixture rows; it is forced
-- again immediately so later guards still observe the production posture.
grant snapshot_verifier_allowlist_owner to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_owner;
alter table private.snapshot_verifier_allowlist no force row level security;
insert into private.snapshot_verifier_allowlist (user_id, workspace_id)
values ('60000000-0000-4000-8000-000000000006', 'a1000000-0000-4000-8000-000000000001'), ('70000000-0000-4000-8000-000000000007', 'b3000000-0000-4000-8000-000000000003');
alter table private.snapshot_verifier_allowlist force row level security;
reset role;
revoke snapshot_verifier_allowlist_owner from postgres;

create temporary table obs (name text primary key, value text not null);
grant insert, select on obs to authenticated, anon, snapshot_verifier,
    snapshot_verifier_allowlist_manager;
grant select on ids to authenticated, anon;

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

-- The NOLOGIN manager is exercised only by SET ROLE in this rolled-back test.
-- Its trigger records the PostgreSQL session identity, not a human identity.
-- The runner holds only the PostgreSQL auto-grant (ADMIN, no SET), so a
-- temporary SET membership is added for the duration of this block.
grant snapshot_verifier_allowlist_manager to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_manager;
insert into private.snapshot_verifier_allowlist (user_id, workspace_id)
values ('30000000-0000-4000-8000-000000000003',
        'a1000000-0000-4000-8000-000000000001');
delete from private.snapshot_verifier_allowlist
where user_id = '30000000-0000-4000-8000-000000000003'
  and workspace_id = 'a1000000-0000-4000-8000-000000000001';
select pg_temp.try('audit_manager_update',
    'update private.snapshot_verifier_allowlist_audit set operation = ''INSERT''');
select pg_temp.try('audit_manager_delete',
    'delete from private.snapshot_verifier_allowlist_audit');
select pg_temp.try('audit_manager_truncate',
    'truncate table private.snapshot_verifier_allowlist_audit');
-- The manager is the only row-level reader of the audit log. Capture the rows
-- it is allowed to see, then assert on them from the pgTAP scope (pgTAP lives
-- in the extensions schema, which the NOLOGIN manager cannot reach by design).
create temporary table audit_events_fixture as
select operation, actor_session_user, user_id, workspace_id
from private.snapshot_verifier_allowlist_audit;
reset role;
revoke snapshot_verifier_allowlist_manager from postgres;
select is(
    (select string_agg(operation, ',' order by operation)
     from audit_events_fixture
     where user_id = '30000000-0000-4000-8000-000000000003'
       and workspace_id = 'a1000000-0000-4000-8000-000000000001'),
    'DELETE,INSERT', 'allowlist INSERT and DELETE each produce an audit event'
);
select ok(
    (select bool_and(actor_session_user = session_user::name)
     from audit_events_fixture
     where user_id = '30000000-0000-4000-8000-000000000003'
       and workspace_id = 'a1000000-0000-4000-8000-000000000001'),
    'audit records the database session identity and does not claim human attribution'
);
select is((select value from obs where name = 'audit_manager_update'), '42501',
    'the row manager cannot update audit records');
select is((select value from obs where name = 'audit_manager_delete'), '42501',
    'the row manager cannot delete audit records');
select is((select value from obs where name = 'audit_manager_truncate'), '42501',
    'the row manager cannot truncate audit records');

-- Non-allowlisted callers ---------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims', '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('outsider_begin', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('outsider_publish', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.cnt('outsider_reads_staged_objects', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
create temporary table snapshot_verifier_allowlist (user_id uuid, workspace_id uuid);
insert into snapshot_verifier_allowlist values ('30000000-0000-4000-8000-000000000003', 'a1000000-0000-4000-8000-000000000001');
set local search_path = pg_temp, public;
select pg_temp.try('outsider_search_path_shadow', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset search_path;
reset role;
set local role anon;
select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims', '{"sub":"30000000-0000-4000-8000-000000000003","role":"anon","aud":"anon"}', true);
select pg_temp.try('anon_begin', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
reset role;
select is((select value from obs where name = 'outsider_begin'), '42501', 'a non-allowlisted user cannot begin verification');
select is((select value from obs where name = 'outsider_publish'), '42501', 'a non-allowlisted user cannot publish');
select is((select value from obs where name = 'outsider_reads_staged_objects'), '0', 'a non-allowlisted user cannot read staged Storage objects');
select is((select value from obs where name = 'outsider_search_path_shadow'), '42501', 'a shadowing temp table cannot fake an allowlist entry');
select is((select value from obs where name = 'anon_begin'), '42501', 'anon cannot call the verifier RPC');

-- Allowlisted verifier ------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('verifier_begin_v1', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('verifier_begin_wrong_workspace', $q$ select * from public.begin_snapshot_verification('b3000000-0000-4000-8000-000000000003', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('verifier_begin_size_mismatch', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd2000000-0000-4000-8000-000000000002') $q$);
select pg_temp.try('verifier_begin_missing_object', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd3000000-0000-4000-8000-000000000003') $q$);
select pg_temp.cnt('verifier_reads_staged_objects', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
select pg_temp.try('verifier_edits_allowlist', $q$ insert into private.snapshot_verifier_allowlist (user_id, workspace_id) values ('30000000-0000-4000-8000-000000000003', 'a1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('verifier_reads_allowlist', $q$ select * from private.snapshot_verifier_allowlist $q$);
select pg_temp.try('verifier_updates_catalog', $q$ update public.source_snapshots set state = 'published' where version_id = 'd1000000-0000-4000-8000-000000000001' $q$);
select pg_temp.try('verifier_assumes_owner_role', $q$ set local role snapshot_publisher_owner $q$);
select pg_temp.try('publish_wrong_digest', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.try('publish_wrong_size', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 99, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.try('publish_wrong_version', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-x') $q$);
select pg_temp.try('publish_other_objects_id', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v9'), 'ver-1') $q$);
select pg_temp.try('publish_unknown_snapshot', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd3000000-0000-4000-8000-000000000003', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.try('publish_bad_digest_format', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset role;
select is((select value from obs where name = 'verifier_begin_v1'), '1', 'the allowlisted verifier receives exactly one claim for a staged snapshot');
select is((select value from obs where name = 'verifier_begin_wrong_workspace'), '42501', 'a verifier scoped to workspace A cannot begin verification in workspace B');
select is((select value from obs where name = 'verifier_begin_size_mismatch'), 'P0002', 'a stored object with a different size is not verifiable');
select is((select value from obs where name = 'verifier_begin_missing_object'), 'P0002', 'a staged row without an uploaded object is not verifiable');
select is((select value from obs where name = 'verifier_reads_staged_objects'), '2', 'the workspace-A verifier reads only the staged objects of workspace A (v1, v2): not workspace B (v9), not an object without a catalog row (orphan)');
select is((select value from obs where name = 'verifier_edits_allowlist'), '42501', 'the verifier cannot edit the allowlist');
select is((select value from obs where name = 'verifier_reads_allowlist'), '42501', 'the verifier cannot read the allowlist table directly');
select is((select value from obs where name = 'verifier_updates_catalog'), '42501', 'the verifier cannot update the catalog directly');
select is((select value from obs where name = 'verifier_assumes_owner_role'), '42501', 'the verifier cannot assume the function owner role');
select is((select value from obs where name = 'publish_wrong_digest'), '42501', 'a digest that differs from the staged claim is rejected');
select is((select value from obs where name = 'publish_wrong_size'), '42501', 'a size that differs from the staged claim is rejected');
select is((select value from obs where name = 'publish_wrong_version'), '42501', 'a wrong Storage object version is rejected');
select is((select value from obs where name = 'publish_other_objects_id'), '42501', 'the Storage object id of a different snapshot is rejected');
select is((select value from obs where name = 'publish_unknown_snapshot'), '42501', 'a staged row without a stored object cannot be published');
select is((select value from obs where name = 'publish_bad_digest_format'), '22023', 'a malformed digest is rejected as invalid evidence');
select is((select state from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'), 'staged', 'every rejected attempt left the snapshot staged');

-- Workspace scope of the verifier identity ----------------------------------
-- Allowlist entries are (user, workspace) pairs; the scope cannot be omitted
-- or point nowhere. The inserts run through the NOLOGIN manager identity so the
-- statement reaches the table constraints instead of failing on table
-- privileges; the temporary SET membership is removed immediately after.
grant snapshot_verifier_allowlist_manager to postgres with set true, inherit false, admin false;
set local role snapshot_verifier_allowlist_manager;
select pg_temp.try('allowlist_unscoped_entry', $q$ insert into private.snapshot_verifier_allowlist (user_id) values ('60000000-0000-4000-8000-000000000006') $q$);
select pg_temp.try('allowlist_unknown_workspace', $q$ insert into private.snapshot_verifier_allowlist (user_id, workspace_id) values ('60000000-0000-4000-8000-000000000006', 'ee000000-0000-4000-8000-0000000000ee') $q$);
reset role;
revoke snapshot_verifier_allowlist_manager from postgres;
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('scope_a_begin_b', $q$ select * from public.begin_snapshot_verification('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009') $q$);
select pg_temp.try('scope_a_publish_b', $q$ select public.publish_verified_snapshot('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009', repeat('a', 64), 100, (select id from ids where label = 'v9'), 'ver-9') $q$);
select pg_temp.try('scope_a_begin_own_workspace_with_b_ids', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009') $q$);
select pg_temp.try('scope_null_workspace_begin', $q$ select * from public.begin_snapshot_verification(null, 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('scope_null_workspace_publish', $q$ select public.publish_verified_snapshot(null, 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
-- A shadowing temp allowlist that claims workspace B for this user must not be consulted.
insert into snapshot_verifier_allowlist values ('60000000-0000-4000-8000-000000000006', 'b3000000-0000-4000-8000-000000000003');
set local search_path = pg_temp, public;
select pg_temp.try('scope_shadow_search_path', $q$ select * from public.begin_snapshot_verification('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009') $q$);
reset search_path;
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', '70000000-0000-4000-8000-000000000007', true);
select set_config('request.jwt.claims', '{"sub":"70000000-0000-4000-8000-000000000007","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('scope_b_begin_b', $q$ select * from public.begin_snapshot_verification('b3000000-0000-4000-8000-000000000003', 'c2000000-0000-4000-8000-000000000002', 'd9000000-0000-4000-8000-000000000009') $q$);
select pg_temp.try('scope_b_begin_a', $q$ select * from public.begin_snapshot_verification('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001') $q$);
select pg_temp.try('scope_b_publish_a', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.cnt('scope_b_reads_objects', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
reset role;
-- Defence in depth: even a direct UPDATE by a privileged role cannot bind a verifier scoped to another workspace.
select pg_temp.try('scope_forged_binding_direct_update', $q$ update public.source_snapshots set state = 'published', verified_sha256 = repeat('a', 64), verified_size_bytes = 100, verified_by = '70000000-0000-4000-8000-000000000007', storage_object_id = (select id from ids where label = 'v1'), storage_object_version = 'ver-1' where version_id = 'd1000000-0000-4000-8000-000000000001' $q$);
select ok((select bool_and(is_nullable = 'NO') from information_schema.columns where table_schema = 'private' and table_name = 'snapshot_verifier_allowlist' and column_name in ('user_id', 'workspace_id')), 'allowlist entries require both a user and a workspace');
select is((select value from obs where name = 'allowlist_unscoped_entry'), '23502', 'an allowlist entry without a workspace is rejected');
select is((select value from obs where name = 'allowlist_unknown_workspace'), '23503', 'an allowlist entry for a nonexistent workspace is rejected');
select is((select value from obs where name = 'scope_a_begin_b'), '42501', 'a workspace-A identity cannot begin verification of a workspace-B snapshot');
select is((select value from obs where name = 'scope_a_publish_b'), '42501', 'a workspace-A identity cannot publish a workspace-B snapshot');
select is((select value from obs where name = 'scope_a_begin_own_workspace_with_b_ids'), 'P0002', 'naming its own workspace with workspace-B source and version finds nothing');
select is((select value from obs where name = 'scope_null_workspace_begin'), '42501', 'a missing workspace is refused on begin');
select is((select value from obs where name = 'scope_null_workspace_publish'), '42501', 'a missing workspace is refused on publish');
select is((select value from obs where name = 'scope_shadow_search_path'), '42501', 'a search_path-shadowed allowlist cannot widen the verifier scope');
select is((select value from obs where name = 'scope_b_begin_b'), '1', 'a workspace-B identity can begin verification in workspace B');
select is((select value from obs where name = 'scope_b_begin_a'), '42501', 'a workspace-B identity cannot begin verification in workspace A');
select is((select value from obs where name = 'scope_b_publish_a'), '42501', 'a workspace-B identity cannot publish in workspace A');
select is((select value from obs where name = 'scope_b_reads_objects'), '1', 'the workspace-B verifier sees only the staged object of workspace B through Storage policy');
select is((select value from obs where name = 'scope_forged_binding_direct_update'), '42501', 'the publication trigger rejects a verifier scoped to a different workspace even for a privileged direct UPDATE');
select is((select state from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'), 'staged', 'no cross-scope attempt changed the snapshot');

-- The legacy publication route is closed -----------------------------------
grant snapshot_verifier to postgres with set true;
set local role snapshot_verifier;
select pg_temp.try('legacy_direct_publish', $q$ update public.source_snapshots set state = 'published', verified_sha256 = repeat('a', 64), verified_size_bytes = 100 where version_id = 'd1000000-0000-4000-8000-000000000001' $q$);
select pg_temp.try('legacy_rpc_publish', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset role;
select is((select value from obs where name = 'legacy_direct_publish'), '42501', 'the legacy snapshot_verifier role can no longer publish by direct UPDATE');
select is((select value from obs where name = 'legacy_rpc_publish'), '42501', 'the legacy snapshot_verifier role cannot call the publish RPC');
select is((select state from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'), 'staged', 'the legacy route left the snapshot staged');

-- Replacement between verification and publication ------------------------
-- These two checks exercise ONLY the comparison logic of the publication
-- trigger. A direct UPDATE of storage.objects by the test role is a simulation:
-- it does not show how Supabase Storage replaces an object (upsert, TUS, S3,
-- copy, delete-and-reupload) or that Storage assigns a new version/id in each
-- case. That behaviour is unproven until a live Storage test exists.
update storage.objects set version = 'ver-1-replaced' where bucket_id = 'source-snapshots' and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001';
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('publish_after_replacement', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset role;
select is((select value from obs where name = 'publish_after_replacement'), '42501', 'comparison logic (simulated): a changed Storage object version is rejected');
update storage.objects set version = 'ver-1' where bucket_id = 'source-snapshots' and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001';
update storage.objects set id = gen_random_uuid() where bucket_id = 'source-snapshots' and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001';
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('publish_after_id_change', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset role;
select is((select value from obs where name = 'publish_after_id_change'), '42501', 'comparison logic (simulated): a changed Storage object id is rejected');
update storage.objects set id = (select id from ids where label = 'v1') where bucket_id = 'source-snapshots' and name = 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001';

-- Successful publication ----------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.try('publish_ok', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
select pg_temp.try('publish_twice', $q$ select public.publish_verified_snapshot('a1000000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', repeat('a', 64), 100, (select id from ids where label = 'v1'), 'ver-1') $q$);
reset role;
select is((select value from obs where name = 'publish_ok'), '1', 'the allowlisted verifier publishes with matching evidence');
select is((select value from obs where name = 'publish_twice'), 'P0002', 'a published snapshot cannot be published again');
select is((select state || ':' || verified_by::text || ':' || storage_object_version
    from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'),
    'published:60000000-0000-4000-8000-000000000006:ver-1', 'the published row records the verifier and the examined Storage object');
select is((select state from public.source_snapshots where version_id = 'd2000000-0000-4000-8000-000000000002'), 'staged', 'other staged snapshots are untouched');
select is((select sha256 || object_key || size_bytes::text || created_by::text || workspace_id::text
    from public.source_snapshots where version_id = 'd1000000-0000-4000-8000-000000000001'),
    repeat('a', 64) || 'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001' || '100' || '10000000-0000-4000-8000-000000000001' || 'a1000000-0000-4000-8000-000000000001',
    'publishing through the RPC changed no claim, key, owner or workspace column');
set local role authenticated;
select set_config('request.jwt.claim.sub', '60000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"60000000-0000-4000-8000-000000000006","role":"authenticated","aud":"authenticated"}', true);
select pg_temp.cnt('verifier_reads_after_publish', $q$ select 1 from storage.objects where bucket_id = 'source-snapshots' $q$);
reset role;
select is((select value from obs where name = 'verifier_reads_after_publish'), '1', 'after publication the verifier policy no longer exposes the published object (only the still-staged v2 remains)');

select * from finish();
rollback;
