-- Forward-only hardening for allowlist administration and audit.
--
-- Design constants follow the existing snapshot_publisher_owner and
-- snapshot_verifier naming convention. They are intentionally separate from
-- API roles and publication roles. No human/operator role is provisioned here;
-- assigning an individually attributable operator identity requires a separate
-- operational approval.
--
-- This migration does not validate source_snapshots_published_verifier_binding
-- or rewrite historical migrations. Unknown policies, triggers, grants,
-- ownership, and partial migration states fail closed for operator review.

do $role_preflight$
declare
    role_name text;
    role_oid oid;
    current_is_superuser boolean;
    current_can_create_roles boolean;
begin
    if current_setting('server_version_num')::integer < 170000
       or current_setting('server_version_num')::integer >= 180000 then
        raise exception 'snapshot verifier admin migration requires PostgreSQL 17'
            using errcode = '55000';
    end if;

    select rolsuper, rolcreaterole
      into current_is_superuser, current_can_create_roles
    from pg_catalog.pg_roles where rolname = current_user;

    if not coalesce(current_is_superuser, false)
       and not coalesce(current_can_create_roles, false) then
        raise exception 'migration actor lacks CREATEROLE required for snapshot control roles'
            using errcode = '42501';
    end if;

    if not coalesce(current_is_superuser, false)
       and not pg_catalog.has_schema_privilege(current_user, 'private', 'CREATE') then
        raise exception 'migration actor lacks CREATE on private for controlled ownership transfer'
            using errcode = '42501';
    end if;

    if current_user in ('anon', 'authenticated', 'service_role', 'authenticator',
                        'snapshot_verifier', 'snapshot_publisher_owner') then
        raise exception 'an API or verifier role cannot be the snapshot admin migration actor'
            using errcode = '42501';
    end if;

    if pg_catalog.to_regclass('private.snapshot_verifier_allowlist') is null
       or pg_catalog.to_regclass('public.source_snapshots') is null
       or pg_catalog.to_regclass('storage.objects') is null then
        raise exception 'required snapshot relations are absent; inspect migration history before continuing'
            using errcode = '55000';
    end if;

    if pg_catalog.to_regprocedure('private.assert_snapshot_verifier_allowlist_shape(regclass)') is null
       or pg_catalog.to_regprocedure('private.assert_snapshot_read_policy_inventory(boolean)') is null
       or pg_catalog.to_regprocedure('private.assert_snapshot_verifier_function_acl_safe()') is null then
        raise exception 'the complete 20261022130000 guard state is absent; review migration history'
            using errcode = '55000';
    end if;

    perform private.assert_snapshot_verifier_allowlist_shape(
        'private.snapshot_verifier_allowlist'::pg_catalog.regclass
    );
    perform private.assert_snapshot_read_policy_inventory(true);
    perform private.assert_snapshot_verifier_function_acl_safe();

    if not coalesce(current_is_superuser, false)
       and (select relation.relowner <> current_user::regrole
            from pg_catalog.pg_class as relation
            where relation.oid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass) then
        raise exception 'migration actor is not the current allowlist owner; refusing an unverified ownership transfer'
            using errcode = '42501';
    end if;

    foreach role_name in array array[
        'snapshot_verifier_allowlist_owner',
        'snapshot_verifier_allowlist_manager',
        'snapshot_verifier_audit_owner'
    ] loop
        role_oid := pg_catalog.to_regrole(role_name)::oid;
        if role_oid is null then
            execute format(
                'create role %I nologin noinherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication',
                role_name
            );
        else
            if exists (
                select 1 from pg_catalog.pg_roles as role_row
                where role_row.oid = role_oid
                  and (role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
                       or role_row.rolbypassrls or role_row.rolcreatedb
                       or role_row.rolcreaterole or role_row.rolreplication)
            ) then
                raise exception 'snapshot control role has unsafe attributes: %', role_name
                    using errcode = '55000';
            end if;
        end if;
    end loop;

    select relowner into role_oid
    from pg_catalog.pg_class
    where oid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass;
    if role_oid not in (
        current_user::pg_catalog.regrole::oid,
        'snapshot_verifier_allowlist_owner'::pg_catalog.regrole::oid
    ) then
        raise exception 'allowlist has an unexpected existing owner; inspect before transfer'
            using errcode = '55000';
    end if;
    if role_oid = 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole::oid
       and not coalesce(current_is_superuser, false) then
        raise exception 'a partially transferred allowlist requires a verified superuser migration actor'
            using errcode = '42501';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where (
            membership.roleid in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            )
            or membership.member in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            )
        )
        and not (
            -- Tolerate the ADMIN-only edge that PostgreSQL records when a
            -- non-superuser CREATEROLE actor creates the role.
            membership.roleid in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            )
            and membership.member = current_user::pg_catalog.regrole
            and membership.admin_option
            and not membership.set_option
            and not membership.inherit_option
        )
    ) then
        raise exception 'snapshot control roles already have memberships; inspect the partial state'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_roles as api_role
        where api_role.rolname in ('anon', 'authenticated', 'service_role', 'authenticator')
          and (
              pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_owner', 'MEMBER')
              or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_manager', 'MEMBER')
              or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_audit_owner', 'MEMBER')
          )
    ) then
        raise exception 'an API role can assume a snapshot control role'
            using errcode = '55000';
    end if;

    if exists (
        select 1 from pg_catalog.pg_shdepend as dependency
        where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and dependency.refobjid in (
              'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
              'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
              'snapshot_verifier_audit_owner'::pg_catalog.regrole
          )
          and dependency.deptype = 'o'
          and not (
              dependency.refobjid = 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
              and dependency.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
              and dependency.objid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass
          )
    ) then
        raise exception 'snapshot control role owns an unapproved object'
            using errcode = '55000';
    end if;

    if pg_catalog.has_schema_privilege(
           'snapshot_verifier_allowlist_owner', 'private', 'CREATE'
       ) or pg_catalog.has_schema_privilege(
           'snapshot_verifier_audit_owner', 'private', 'CREATE'
       ) or pg_catalog.has_schema_privilege(
           'snapshot_verifier_allowlist_manager', 'private', 'CREATE'
       ) then
        raise exception 'snapshot control role has a pre-existing CREATE privilege on private'
            using errcode = '55000';
    end if;

    if exists (select 1 from pg_catalog.pg_policy
        where polrelid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass
          and polname in ('snapshot_verifier_allowlist_select_manager',
                          'snapshot_verifier_allowlist_insert_manager',
                          'snapshot_verifier_allowlist_delete_manager'))
       or exists (select 1 from pg_catalog.pg_trigger
        where tgrelid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass
          and not tgisinternal) then
        raise exception 'allowlist already has manager policies or triggers; inspect the partial state'
            using errcode = '55000';
    end if;

    if not coalesce(current_is_superuser, false)
       and not exists (
           select 1 from pg_catalog.pg_namespace as schema_row
           where schema_row.nspname = 'private'
             and (schema_row.nspowner = current_user::pg_catalog.regrole
                  or exists (
                      select 1 from pg_catalog.aclexplode(
                          coalesce(schema_row.nspacl,
                              pg_catalog.acldefault('n', schema_row.nspowner))
                      ) as acl
                      where acl.grantee = current_user::pg_catalog.regrole
                        and acl.privilege_type = 'CREATE'
                        and acl.is_grantable
                  ))
       ) then
        raise exception 'migration actor cannot grant temporary CREATE on private'
            using errcode = '42501';
    end if;

    if pg_catalog.to_regclass('private.snapshot_verifier_allowlist_audit') is not null
       or pg_catalog.to_regclass('private.snapshot_verifier_read_policy_baseline') is not null
       or pg_catalog.to_regprocedure('private.audit_snapshot_verifier_allowlist_change()') is not null
       or pg_catalog.to_regprocedure('private.assert_snapshot_verifier_allowlist_admin_shape(regclass)') is not null
       or pg_catalog.to_regprocedure('private.assert_snapshot_read_policy_inventory_v2(boolean)') is not null then
        raise exception 'snapshot admin migration has a partial prior application; inspect before retrying'
            using errcode = '55000';
    end if;
end;
$role_preflight$;

-- Compare actual PostgreSQL 17 deparse output with independently parsed
-- expected policies. The temporary relations deliberately use the same names
-- and columns so pg_get_expr can canonicalize both predicates for comparison.
create temp table source_snapshots (like public.source_snapshots including defaults);
create temp table objects (like storage.objects including defaults);
create temp table expected_binding (
    state text,
    verified_by uuid,
    storage_object_id uuid,
    storage_object_version text,
    constraint expected_snapshot_binding check (
        state <> 'published'
        or (verified_by is not null and storage_object_id is not null
            and storage_object_version is not null
            and length(storage_object_version) between 1 and 512)
    )
);
alter table pg_temp.source_snapshots enable row level security;
alter table pg_temp.objects enable row level security;

create policy expected_source_snapshot_verifier_role_gate_restrictive_fence
    on pg_temp.source_snapshots as restrictive for select to public
    using (
        current_user = 'authenticated'::pg_catalog.name
        or current_user = 'snapshot_publisher_owner'::pg_catalog.name
    );

create policy expected_source_snapshot_authenticated_restrictive_fence
    on pg_temp.source_snapshots as restrictive for select to authenticated
    using (
        (
            (source_snapshots.state = 'published'
             and source_snapshots.verified_by is not null
             and source_snapshots.storage_object_id is not null
             and source_snapshots.storage_object_version is not null
             and length(source_snapshots.storage_object_version) between 1 and 512
             and exists (
                 select 1 from workspace_memberships as membership
                 join source_grants as source_grant
                   on source_grant.workspace_id = membership.workspace_id
                  and source_grant.source_id = source_snapshots.source_id
                 where membership.workspace_id = source_snapshots.workspace_id
                   and membership.user_id = (select auth.uid())
                   and membership.status = 'active'
                   and source_grant.principal_id = (select auth.uid())
                   and source_grant.grant_type = 'read'
             ))
            or (source_snapshots.state = 'staged'
                and source_snapshots.created_by = (select auth.uid())
                and exists (
                    select 1 from workspace_memberships as membership
                    where membership.workspace_id = source_snapshots.workspace_id
                      and membership.user_id = (select auth.uid())
                      and membership.status = 'active'
                ))
        )
    );

create policy expected_source_snapshot_publisher_owner_restrictive_fence
    on pg_temp.source_snapshots as restrictive for select to snapshot_publisher_owner
    using (
        source_snapshots.state in ('staged', 'published')
        and exists (
            select 1 from private.snapshot_verifier_allowlist as allowed
            where allowed.user_id = (select auth.uid())
              and allowed.workspace_id = source_snapshots.workspace_id
        )
    );

create policy expected_storage_snapshot_verifier_role_gate_restrictive_fence
    on pg_temp.objects as restrictive for select to public
    using (
        objects.bucket_id is distinct from 'source-snapshots'
        or current_user = 'authenticated'::pg_catalog.name
        or current_user = 'snapshot_publisher_owner'::pg_catalog.name
    );

create policy expected_storage_snapshot_authenticated_restrictive_fence
    on pg_temp.objects as restrictive for select to authenticated
    using (
        objects.bucket_id is distinct from 'source-snapshots'
        or (exists (
                select 1
                from public.source_snapshots as snapshot
                join workspace_memberships as membership
                  on membership.workspace_id = snapshot.workspace_id
                join source_grants as source_grant
                  on source_grant.workspace_id = snapshot.workspace_id
                 and source_grant.source_id = snapshot.source_id
                where snapshot.object_key = objects.name
                  and snapshot.state = 'published'
                  and snapshot.verified_by is not null
                  and snapshot.storage_object_id = objects.id
                  and snapshot.storage_object_version = objects.version
                  and objects.owner_id = snapshot.created_by::text
                  and (objects.metadata ->> 'size')::bigint = snapshot.size_bytes
                  and membership.user_id = (select auth.uid())
                  and membership.status = 'active'
                  and source_grant.principal_id = (select auth.uid())
                  and source_grant.grant_type = 'read'
            ) or verifier_may_read_staged_object(objects.name))
    );

create policy expected_storage_snapshot_publisher_owner_restrictive_fence
    on pg_temp.objects as restrictive for select to snapshot_publisher_owner
    using (
        objects.bucket_id = 'source-snapshots'
        and exists (
            select 1
            from public.source_snapshots as snapshot
            join private.snapshot_verifier_allowlist as allowed
              on allowed.workspace_id = snapshot.workspace_id
            where snapshot.object_key = objects.name
              and snapshot.state = 'staged'
              and allowed.user_id = (select auth.uid())
              and objects.owner_id = snapshot.created_by::text
              and (objects.metadata ->> 'size')::bigint = snapshot.size_bytes
        )
    );

do $predicate_preflight$
declare
    actual_expression text;
    expected_expression text;
    actual_count integer;
    expected_binding_expression text;
    actual_binding_expression text;
    fence record;
begin
    if not (
        select count(*) = 3
        from pg_catalog.pg_policy
        where polrelid = 'public.source_snapshots'::pg_catalog.regclass
          and polcmd = 'r' and not polpermissive and polwithcheck is null
          and (
              (polname = 'source_snapshots_select_verifier_role_gate_restrictive_fence'
               and polroles = array[0::oid])
              or (polname = 'source_snapshots_select_authenticated_restrictive_fence'
                  and polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (polname = 'source_snapshots_select_publisher_owner_restrictive_fence'
                  and polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
          )
    ) or not (
        select count(*) = 3
        from pg_catalog.pg_policy
        where polrelid = 'storage.objects'::pg_catalog.regclass
          and polcmd = 'r' and not polpermissive and polwithcheck is null
          and (
              (polname = 'source_snapshots_objects_verifier_role_gate_restrictive_fence'
               and polroles = array[0::oid])
              or (polname = 'source_snapshots_objects_select_authenticated_restrictive_fence'
                  and polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (polname = 'source_snapshots_objects_publisher_owner_restrictive_fence'
                  and polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
          )
    ) then
        raise exception 'restrictive snapshot fences have incompatible metadata'
            using errcode = '55000';
    end if;

    for fence in
        select actual_relation, actual_name, expected_name
        from (values
            ('public.source_snapshots'::pg_catalog.regclass,
             'source_snapshots_select_verifier_role_gate_restrictive_fence'::pg_catalog.name,
             'expected_source_snapshot_verifier_role_gate_restrictive_fence'::pg_catalog.name),
            ('public.source_snapshots'::pg_catalog.regclass,
             'source_snapshots_select_authenticated_restrictive_fence'::pg_catalog.name,
             'expected_source_snapshot_authenticated_restrictive_fence'::pg_catalog.name),
            ('public.source_snapshots'::pg_catalog.regclass,
             'source_snapshots_select_publisher_owner_restrictive_fence'::pg_catalog.name,
             'expected_source_snapshot_publisher_owner_restrictive_fence'::pg_catalog.name),
            ('storage.objects'::pg_catalog.regclass,
             'source_snapshots_objects_verifier_role_gate_restrictive_fence'::pg_catalog.name,
             'expected_storage_snapshot_verifier_role_gate_restrictive_fence'::pg_catalog.name),
            ('storage.objects'::pg_catalog.regclass,
             'source_snapshots_objects_select_authenticated_restrictive_fence'::pg_catalog.name,
             'expected_storage_snapshot_authenticated_restrictive_fence'::pg_catalog.name),
            ('storage.objects'::pg_catalog.regclass,
             'source_snapshots_objects_publisher_owner_restrictive_fence'::pg_catalog.name,
             'expected_storage_snapshot_publisher_owner_restrictive_fence'::pg_catalog.name)
        ) as approved(actual_relation, actual_name, expected_name)
    loop
        select pg_catalog.pg_get_expr(actual.polqual, actual.polrelid, true),
               pg_catalog.pg_get_expr(expected.polqual, expected.polrelid, true)
          into actual_expression, expected_expression
        from pg_catalog.pg_policy as actual
        join pg_catalog.pg_policy as expected
          on expected.polname = fence.expected_name
         and expected.polrelid = case fence.actual_relation
               when 'public.source_snapshots'::pg_catalog.regclass
                 then 'pg_temp.source_snapshots'::pg_catalog.regclass
               else 'pg_temp.objects'::pg_catalog.regclass
             end
        where actual.polrelid = fence.actual_relation
          and actual.polname = fence.actual_name;
        if actual_expression is null
           or actual_expression is distinct from expected_expression then
            raise exception 'restrictive fence % predicate differs from the approved PostgreSQL 17 expression',
                    fence.actual_name
                using errcode = '55000';
        end if;
    end loop;

    select pg_catalog.pg_get_expr(constraint_row.conbin, constraint_row.conrelid, true)
      into actual_binding_expression
    from pg_catalog.pg_constraint as constraint_row
    where constraint_row.conrelid = 'public.source_snapshots'::pg_catalog.regclass
      and constraint_row.conname = 'source_snapshots_published_verifier_binding'
      and constraint_row.contype = 'c'
      and not constraint_row.condeferrable;
    select pg_catalog.pg_get_expr(constraint_row.conbin, constraint_row.conrelid, true)
      into expected_binding_expression
    from pg_catalog.pg_constraint as constraint_row
    where constraint_row.conrelid = 'pg_temp.expected_binding'::pg_catalog.regclass
      and constraint_row.conname = 'expected_snapshot_binding';
    if actual_binding_expression is null
       or actual_binding_expression is distinct from expected_binding_expression then
        raise exception 'source_snapshots_published_verifier_binding definition differs from the approved constraint'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_trigger as trigger_row
        where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
          and not trigger_row.tgisinternal) <> 4
       or not exists (select 1 from pg_catalog.pg_trigger as trigger_row
          where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
            and trigger_row.tgname = 'source_snapshots_protect_immutability'
            and trigger_row.tgfoid = 'private.protect_source_snapshot()'::pg_catalog.regprocedure
            and trigger_row.tgtype = 23 and trigger_row.tgenabled = 'O'
            and not trigger_row.tgisinternal)
       or not exists (select 1 from pg_catalog.pg_trigger as trigger_row
          where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
            and trigger_row.tgname = 'source_snapshots_reject_delete'
            and trigger_row.tgfoid = 'private.reject_source_snapshot_delete()'::pg_catalog.regprocedure
            and trigger_row.tgtype = 11 and trigger_row.tgenabled = 'O'
            and not trigger_row.tgisinternal)
       or not exists (select 1 from pg_catalog.pg_trigger as trigger_row
          where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
            and trigger_row.tgname = 'source_snapshots_require_stored_object'
            and trigger_row.tgfoid = 'private.require_stored_snapshot_object()'::pg_catalog.regprocedure
            and trigger_row.tgtype = 19 and trigger_row.tgenabled = 'O'
            and not trigger_row.tgisinternal)
       or not exists (select 1 from pg_catalog.pg_trigger as trigger_row
          where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
            and trigger_row.tgname = 'source_snapshots_require_verification_binding'
            and trigger_row.tgfoid = 'private.require_verification_binding()'::pg_catalog.regprocedure
            and trigger_row.tgtype = 19 and trigger_row.tgenabled = 'O'
            and not trigger_row.tgisinternal) then
        raise exception 'snapshot protection triggers are missing, altered, disabled, or accompanied by an unknown trigger'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_constraint
        where conrelid = 'public.source_snapshots'::pg_catalog.regclass
          and conname = 'source_snapshots_published_verifier_binding'
          and contype = 'c' and not condeferrable) <> 1 then
        raise exception 'published verifier binding constraint is missing or duplicated'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_trigger as trigger_row
        join pg_catalog.pg_proc as function_row on function_row.oid = trigger_row.tgfoid
        where trigger_row.tgrelid = 'public.source_snapshots'::pg_catalog.regclass
          and not trigger_row.tgisinternal
          and (function_row.prosecdef
            or not (function_row.proconfig @> array['search_path=""'])
            or function_row.proowner in (
                'anon'::pg_catalog.regrole,
                'authenticated'::pg_catalog.regrole,
                'service_role'::pg_catalog.regrole,
                'snapshot_verifier'::pg_catalog.regrole
            )
            or pg_catalog.has_function_privilege('anon', function_row.oid, 'EXECUTE')
            or pg_catalog.has_function_privilege('authenticated', function_row.oid, 'EXECUTE')
            or pg_catalog.has_function_privilege('service_role', function_row.oid, 'EXECUTE')
            or pg_catalog.has_function_privilege('snapshot_verifier', function_row.oid, 'EXECUTE'))
    ) then
        raise exception 'snapshot protection trigger function ownership, search_path, or EXECUTE ACL is unsafe'
            using errcode = '42501';
    end if;

    select count(*)::integer into actual_count
    from pg_catalog.pg_policy
    where (polrelid = 'public.source_snapshots'::pg_catalog.regclass
           and polcmd in ('r', '*'))
       or (polrelid = 'storage.objects'::pg_catalog.regclass
           and polcmd in ('r', '*'));
    if actual_count <> 11 then
        raise exception 'snapshot read policy inventory is incomplete or contains unknown policies'
            using errcode = '55000';
    end if;
end;
$predicate_preflight$;

create table private.snapshot_verifier_read_policy_baseline (
    relation_oid oid not null,
    policy_name name not null,
    command text not null,
    is_permissive boolean not null,
    roles oid[] not null,
    using_expression text not null,
    primary key (relation_oid, policy_name)
);
revoke all on table private.snapshot_verifier_read_policy_baseline
    from public, anon, authenticated, service_role, snapshot_verifier,
         snapshot_publisher_owner, snapshot_verifier_allowlist_manager,
         snapshot_verifier_audit_owner;

-- The baseline predicates must be deparsed under the exact search_path used by
-- private.assert_snapshot_read_policy_inventory_v2 (which is ''), otherwise
-- pg_get_expr qualifies public.* relations differently and the later
-- comparison reports a false mismatch.
set search_path = '';

insert into private.snapshot_verifier_read_policy_baseline
    (relation_oid, policy_name, command, is_permissive, roles, using_expression)
select actual.polrelid, actual.polname, actual.polcmd::text,
       actual.polpermissive, actual.polroles,
       pg_catalog.pg_get_expr(expected.polqual, expected.polrelid, true)
from pg_catalog.pg_policy as actual
join (values
    ('public.source_snapshots'::pg_catalog.regclass,
     'source_snapshots_select_verifier_role_gate_restrictive_fence'::pg_catalog.name,
     'pg_temp.source_snapshots'::pg_catalog.regclass,
     'expected_source_snapshot_verifier_role_gate_restrictive_fence'::pg_catalog.name),
    ('public.source_snapshots'::pg_catalog.regclass,
     'source_snapshots_select_authenticated_restrictive_fence'::pg_catalog.name,
     'pg_temp.source_snapshots'::pg_catalog.regclass,
     'expected_source_snapshot_authenticated_restrictive_fence'::pg_catalog.name),
    ('public.source_snapshots'::pg_catalog.regclass,
     'source_snapshots_select_publisher_owner_restrictive_fence'::pg_catalog.name,
     'pg_temp.source_snapshots'::pg_catalog.regclass,
     'expected_source_snapshot_publisher_owner_restrictive_fence'::pg_catalog.name),
    ('storage.objects'::pg_catalog.regclass,
     'source_snapshots_objects_verifier_role_gate_restrictive_fence'::pg_catalog.name,
     'pg_temp.objects'::pg_catalog.regclass,
     'expected_storage_snapshot_verifier_role_gate_restrictive_fence'::pg_catalog.name),
    ('storage.objects'::pg_catalog.regclass,
     'source_snapshots_objects_select_authenticated_restrictive_fence'::pg_catalog.name,
     'pg_temp.objects'::pg_catalog.regclass,
     'expected_storage_snapshot_authenticated_restrictive_fence'::pg_catalog.name),
    ('storage.objects'::pg_catalog.regclass,
     'source_snapshots_objects_publisher_owner_restrictive_fence'::pg_catalog.name,
     'pg_temp.objects'::pg_catalog.regclass,
     'expected_storage_snapshot_publisher_owner_restrictive_fence'::pg_catalog.name)
) as approved(actual_relation, actual_name, expected_relation, expected_name)
  on actual.polrelid = approved.actual_relation
 and actual.polname = approved.actual_name
join pg_catalog.pg_policy as expected
  on expected.polrelid = approved.expected_relation
 and expected.polname = approved.expected_name;

reset search_path;

create or replace function private.assert_snapshot_read_policy_inventory_v2(
    p_fences_required boolean
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    source_relation oid := 'public.source_snapshots'::pg_catalog.regclass;
    storage_relation oid := 'storage.objects'::pg_catalog.regclass;
    source_count integer;
    storage_count integer;
begin
    if exists (
        select 1 from pg_catalog.pg_policy as policy
        where policy.polrelid = source_relation and policy.polcmd in ('r', '*')
          and not (
              (policy.polname = 'source_snapshots_select_granted_published_or_own_staged'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (policy.polname = 'source_snapshots_select_publisher_owner'
                  and policy.polcmd = 'r' and policy.polpermissive
                  and policy.polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_select_verifier_role_gate_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array[0::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_select_authenticated_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_select_publisher_owner_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
          )
    ) or exists (
        select 1 from pg_catalog.pg_policy as policy
        where policy.polrelid = storage_relation and policy.polcmd in ('r', '*')
          and not (
              (policy.polname = 'source_snapshots_objects_select_granted_published'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (policy.polname = 'source_snapshots_objects_select_allowlisted_verifier'
                  and policy.polcmd = 'r' and policy.polpermissive
                  and policy.polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (policy.polname = 'source_snapshots_objects_select_publisher_owner'
                  and policy.polcmd = 'r' and policy.polpermissive
                  and policy.polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_objects_verifier_role_gate_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array[0::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_objects_select_authenticated_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array['authenticated'::pg_catalog.regrole::oid])
              or (p_fences_required
                  and policy.polname = 'source_snapshots_objects_publisher_owner_restrictive_fence'
                  and policy.polcmd = 'r' and not policy.polpermissive
                  and policy.polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid])
          )
    ) then
        raise exception 'unapproved SELECT or ALL policy exists on a snapshot read relation'
            using errcode = '55000';
    end if;

    select count(*)::integer into source_count from pg_catalog.pg_policy
    where polrelid = source_relation and polcmd in ('r', '*');
    select count(*)::integer into storage_count from pg_catalog.pg_policy
    where polrelid = storage_relation and polcmd in ('r', '*');
    if source_count <> (case when p_fences_required then 5 else 2 end)
       or storage_count <> (case when p_fences_required then 6 else 3 end) then
        raise exception 'snapshot read policy inventory is incomplete'
            using errcode = '55000';
    end if;

    if p_fences_required and exists (
        select 1
        from private.snapshot_verifier_read_policy_baseline as expected
        left join pg_catalog.pg_policy as actual
          on actual.polrelid = expected.relation_oid
         and actual.polname = expected.policy_name
        where actual.oid is null
           or actual.polcmd::text is distinct from expected.command
           or actual.polpermissive is distinct from expected.is_permissive
           or actual.polroles is distinct from expected.roles
           or pg_catalog.pg_get_expr(actual.polqual, actual.polrelid, true)
                is distinct from expected.using_expression
           or actual.polwithcheck is not null
    ) then
        raise exception 'snapshot read restrictive fence definition changed'
            using errcode = '55000';
    end if;

    if not (select relrowsecurity and relforcerowsecurity
            from pg_catalog.pg_class where oid = source_relation)
       or not (select relrowsecurity from pg_catalog.pg_class where oid = storage_relation) then
        raise exception 'snapshot read relations must keep row level security enabled'
            using errcode = '55000';
    end if;
end;
$function$;
revoke all on function private.assert_snapshot_read_policy_inventory_v2(boolean)
    from public, anon, authenticated, service_role, snapshot_verifier,
         snapshot_publisher_owner, snapshot_verifier_allowlist_manager,
         snapshot_verifier_audit_owner;

create or replace function private.assert_snapshot_verifier_allowlist_admin_shape(
    p_relation pg_catalog.regclass
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    relation_oid oid := p_relation::oid;
    auth_users_oid oid;
    user_attnum smallint;
    workspace_attnum smallint;
    added_at_attnum smallint;
    policy_count integer;
begin
    if relation_oid <> 'private.snapshot_verifier_allowlist'::pg_catalog.regclass then
        perform private.assert_snapshot_verifier_allowlist_shape(p_relation);
        return;
    end if;

    -- Resolve auth.users through the catalogs instead of casting
    -- 'auth.users'::regclass, because this security-definer function runs as
    -- snapshot_verifier_allowlist_owner, which intentionally has no USAGE on
    -- the auth schema.
    select class_row.oid into auth_users_oid
    from pg_catalog.pg_class as class_row
    join pg_catalog.pg_namespace as schema_row
      on schema_row.oid = class_row.relnamespace
    where schema_row.nspname = 'auth'
      and class_row.relname = 'users'
      and class_row.relkind in ('r', 'p');
    if auth_users_oid is null then
        raise exception 'auth.users relation is unavailable for allowlist FK verification'
            using errcode = '55000';
    end if;

    select attnum into user_attnum from pg_catalog.pg_attribute
    where attrelid = relation_oid and attname = 'user_id'
      and atttypid = 'pg_catalog.uuid'::pg_catalog.regtype
      and attnotnull and not attisdropped;
    select attnum into workspace_attnum from pg_catalog.pg_attribute
    where attrelid = relation_oid and attname = 'workspace_id'
      and atttypid = 'pg_catalog.uuid'::pg_catalog.regtype
      and attnotnull and not attisdropped;
    select attnum into added_at_attnum from pg_catalog.pg_attribute
    where attrelid = relation_oid and attname = 'added_at'
      and atttypid = 'pg_catalog.timestamptz'::pg_catalog.regtype
      and attnotnull and not attisdropped;

    if (select count(*) from pg_catalog.pg_attribute
        where attrelid = relation_oid and attnum > 0 and not attisdropped) <> 3
       or exists (select 1 from pg_catalog.pg_attribute
                  where attrelid = relation_oid and attnum > 0 and attisdropped)
       or user_attnum is null or workspace_attnum is null or added_at_attnum is null
       or not exists (select 1 from pg_catalog.pg_attrdef
           where adrelid = relation_oid and adnum = added_at_attnum
             and pg_catalog.pg_get_expr(adbin, adrelid) in
                 ('now()', 'CURRENT_TIMESTAMP', 'transaction_timestamp()')) then
        raise exception 'snapshot verifier allowlist has incompatible columns or defaults'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_constraint
        where conrelid = relation_oid and contype = 'p'
          and conkey = array[user_attnum, workspace_attnum]::smallint[]) <> 1
       or (select count(*) from pg_catalog.pg_constraint
           where conrelid = relation_oid and contype = 'p') <> 1 then
        raise exception 'snapshot verifier allowlist has an incompatible primary key'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_constraint
        where conrelid = relation_oid and contype = 'f'
          and convalidated and not condeferrable and confdeltype = 'c'
          and confupdtype = 'a'
          and ((conkey = array[user_attnum]::smallint[]
                and confrelid = auth_users_oid
                and confkey = array[(
                    select attnum from pg_catalog.pg_attribute
                    where attrelid = auth_users_oid
                      and attname = 'id' and not attisdropped
                )]::smallint[])
            or (conkey = array[workspace_attnum]::smallint[]
                and confrelid = 'public.workspaces'::pg_catalog.regclass
                and confkey = array[(
                    select attnum from pg_catalog.pg_attribute
                    where attrelid = 'public.workspaces'::pg_catalog.regclass
                      and attname = 'id' and not attisdropped
                )]::smallint[]))) <> 2
       or (select count(*) from pg_catalog.pg_constraint
           where conrelid = relation_oid and contype = 'f') <> 2
       or (select count(*) from pg_catalog.pg_constraint where conrelid = relation_oid) <> 3 then
        raise exception 'snapshot verifier allowlist has incompatible foreign keys or constraints'
            using errcode = '55000';
    end if;

    if not exists (select 1 from pg_catalog.pg_class
        where oid = relation_oid and relkind = 'r'
          and relrowsecurity and relforcerowsecurity
          and relowner = 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole) then
        raise exception 'snapshot verifier allowlist has incompatible ownership or RLS state'
            using errcode = '55000';
    end if;

    if exists (
        select 1 from pg_catalog.pg_roles as role_row
        where role_row.rolname in (
            'snapshot_verifier_allowlist_owner',
            'snapshot_verifier_allowlist_manager',
            'snapshot_verifier_audit_owner'
        ) and (role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
               or role_row.rolbypassrls or role_row.rolcreatedb
               or role_row.rolcreaterole or role_row.rolreplication)
    ) or exists (
        select 1 from pg_catalog.pg_auth_members as membership
        where (
            membership.roleid in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            ) or membership.member in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            )
        )
        and not (
            -- Tolerate the edges PostgreSQL records for the migration actor
            -- (session_user) when it creates the control roles and then
            -- temporarily grants itself SET membership to transfer ownership:
            --   * CREATE ROLE auto-grant: admin_option, no set, no inherit
            --   * temporary ownership-transfer grant: set_option, no admin
            membership.roleid in (
                'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                'snapshot_verifier_allowlist_manager'::pg_catalog.regrole,
                'snapshot_verifier_audit_owner'::pg_catalog.regrole
            )
            and membership.member = session_user::pg_catalog.regrole
            and not membership.inherit_option
            and (
                (membership.admin_option and not membership.set_option)
                or (membership.set_option and not membership.admin_option)
            )
        )
    ) then
        raise exception 'snapshot control role attributes or membership graph are unsafe'
            using errcode = '55000';
    end if;

    if exists (
        select 1 from pg_catalog.pg_roles as api_role
        where api_role.rolname in ('anon', 'authenticated', 'service_role',
                                   'authenticator', 'snapshot_verifier',
                                   'snapshot_publisher_owner')
          and (pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_owner', 'MEMBER')
            or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_manager', 'MEMBER')
            or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_audit_owner', 'MEMBER'))
    ) or pg_catalog.has_schema_privilege(
            'snapshot_verifier_allowlist_owner', 'private', 'CREATE')
      or pg_catalog.has_schema_privilege(
            'snapshot_verifier_allowlist_manager', 'private', 'CREATE')
      or pg_catalog.has_schema_privilege(
            'snapshot_verifier_audit_owner', 'private', 'CREATE') then
        raise exception 'an API, verifier, publisher, or control role has an unsafe membership path or schema privilege'
            using errcode = '55000';
    end if;

    select count(*)::integer into policy_count from pg_catalog.pg_policy
    where polrelid = relation_oid;
    if policy_count <> 4
       or not exists (select 1 from pg_catalog.pg_policy
           where polrelid = relation_oid and polname = 'snapshot_verifier_allowlist_select_owner'
             and polcmd = 'r' and polpermissive
             and polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid]
             and pg_catalog.pg_get_expr(polqual, polrelid, true) in ('true', '(true)')
             and polwithcheck is null)
       or not exists (select 1 from pg_catalog.pg_policy
           where polrelid = relation_oid and polname = 'snapshot_verifier_allowlist_select_manager'
             and polcmd = 'r' and polpermissive
             and polroles = array['snapshot_verifier_allowlist_manager'::pg_catalog.regrole::oid]
             and pg_catalog.pg_get_expr(polqual, polrelid, true) in ('true', '(true)')
             and polwithcheck is null)
       or not exists (select 1 from pg_catalog.pg_policy
           where polrelid = relation_oid and polname = 'snapshot_verifier_allowlist_insert_manager'
             and polcmd = 'a' and polpermissive
             and polroles = array['snapshot_verifier_allowlist_manager'::pg_catalog.regrole::oid]
             and polqual is null
             and pg_catalog.pg_get_expr(polwithcheck, polrelid, true) in ('true', '(true)'))
       or not exists (select 1 from pg_catalog.pg_policy
           where polrelid = relation_oid and polname = 'snapshot_verifier_allowlist_delete_manager'
             and polcmd = 'd' and polpermissive
             and polroles = array['snapshot_verifier_allowlist_manager'::pg_catalog.regrole::oid]
             and pg_catalog.pg_get_expr(polqual, polrelid, true) in ('true', '(true)')
             and polwithcheck is null) then
        raise exception 'snapshot verifier allowlist has incompatible row policies'
            using errcode = '55000';
    end if;

    if exists (
        select 1 from pg_catalog.pg_roles as role_row
        where role_row.rolname in ('anon', 'authenticated', 'service_role', 'snapshot_verifier')
          and (pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'SELECT')
            or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'INSERT')
            or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'UPDATE')
            or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'DELETE'))
    ) or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'INSERT')
      or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'UPDATE')
      or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'DELETE')
      or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'TRUNCATE')
      or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'REFERENCES')
      or pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'TRIGGER')
      or not pg_catalog.has_table_privilege('snapshot_publisher_owner', relation_oid, 'SELECT')
      or not pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'DELETE')
      or pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'UPDATE')
      or pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'TRUNCATE')
      or pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'REFERENCES')
      or pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'TRIGGER')
      or not pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'user_id', 'SELECT')
      or not pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'workspace_id', 'SELECT')
      or pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'added_at', 'SELECT')
      or not pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'user_id', 'INSERT')
      or not pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'workspace_id', 'INSERT')
      or pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager', relation_oid, 'added_at', 'INSERT') then
        raise exception 'snapshot allowlist role ACLs differ from the approved least-privilege contract'
            using errcode = '42501';
    end if;

    if exists (
        select 1 from pg_catalog.pg_class as relation
        cross join lateral pg_catalog.aclexplode(
            coalesce(relation.relacl, pg_catalog.acldefault('r', relation.relowner))
        ) as acl
        where relation.oid = relation_oid
          and (acl.grantee = 0
            or (acl.grantee not in (
                    'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
                    'snapshot_publisher_owner'::pg_catalog.regrole,
                    'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
                ))
            or (acl.grantee = 'snapshot_publisher_owner'::pg_catalog.regrole
                and (acl.privilege_type <> 'SELECT' or acl.is_grantable))
            or (acl.grantee = 'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
                and (acl.privilege_type <> 'DELETE' or acl.is_grantable)))
    ) or exists (
        select 1
        from pg_catalog.pg_attribute as attribute
        cross join lateral pg_catalog.aclexplode(attribute.attacl) as acl
        where attribute.attrelid = relation_oid
          and attribute.attnum > 0 and not attribute.attisdropped
          and (acl.grantee <> 'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
            or acl.privilege_type not in ('SELECT', 'INSERT')
            or acl.is_grantable
            or attribute.attname not in ('user_id', 'workspace_id'))
    ) or (
        select count(*) from pg_catalog.pg_attribute as attribute
        cross join lateral pg_catalog.aclexplode(attribute.attacl) as acl
        where attribute.attrelid = relation_oid
          and attribute.attnum > 0 and not attribute.attisdropped
          and acl.grantee = 'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
          and acl.privilege_type in ('SELECT', 'INSERT')
    ) <> 4 then
        raise exception 'allowlist ACL contains an unapproved or grantable privilege'
            using errcode = '42501';
    end if;

    if exists (
        select 1 from pg_catalog.pg_class as baseline
        cross join lateral pg_catalog.aclexplode(
            coalesce(baseline.relacl,
                     pg_catalog.acldefault('r', baseline.relowner))
        ) as acl
        where baseline.oid = 'private.snapshot_verifier_read_policy_baseline'::pg_catalog.regclass
          and (acl.grantee <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
               or acl.privilege_type not in ('SELECT', 'INSERT', 'UPDATE', 'DELETE',
                                             'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN'))
    ) or (select relowner <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
          from pg_catalog.pg_class
          where oid = 'private.snapshot_verifier_read_policy_baseline'::pg_catalog.regclass) then
        raise exception 'snapshot read policy baseline has an unexpected owner or ACL'
            using errcode = '42501';
    end if;
end;
$function$;
revoke all on function private.assert_snapshot_verifier_allowlist_admin_shape(pg_catalog.regclass)
    from public, anon, authenticated, service_role, snapshot_verifier,
         snapshot_publisher_owner, snapshot_verifier_allowlist_manager,
         snapshot_verifier_audit_owner;

-- This baseline is internal control data. The migration's later finalization
-- block assigns it to the isolated NOLOGIN owner; no policy is rewritten.

do $atomic_admin_install$
declare
    current_is_superuser boolean;
    existing_owner oid;
begin
    select rolsuper into current_is_superuser
    from pg_catalog.pg_roles where rolname = current_user;

    select relowner into existing_owner
    from pg_catalog.pg_class
    where oid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass;
    if existing_owner <> current_user::pg_catalog.regrole
       and not coalesce(current_is_superuser, false) then
        raise exception 'only the verified current table owner or a superuser may finalize allowlist ownership'
            using errcode = '42501';
    end if;

    if not coalesce(current_is_superuser, false) then
        execute format(
            'grant snapshot_verifier_allowlist_owner to %I with set true, inherit false, admin false',
            current_user
        );
        execute format(
            'grant snapshot_verifier_audit_owner to %I with set true, inherit false, admin false',
            current_user
        );
    end if;

    execute 'grant create on schema private to snapshot_verifier_allowlist_owner';
    execute 'grant create on schema private to snapshot_verifier_audit_owner';
    execute 'grant usage on schema private to snapshot_verifier_allowlist_owner';
    execute 'grant usage on schema private to snapshot_verifier_allowlist_manager';
    execute 'grant usage on schema private to snapshot_verifier_audit_owner';
    -- The owner's verification functions resolve public.source_snapshots and
    -- storage.objects by name, so the owner needs name-lookup access to those
    -- schemas (it still has no table privileges on them).
    execute 'grant usage on schema storage to snapshot_verifier_allowlist_owner';
    execute 'grant usage on schema public to snapshot_verifier_allowlist_owner';

    execute 'grant select (user_id, workspace_id), insert (user_id, workspace_id), delete '
         || 'on table private.snapshot_verifier_allowlist '
         || 'to snapshot_verifier_allowlist_manager';
    execute 'revoke all on table private.snapshot_verifier_allowlist '
         || 'from public, anon, authenticated, service_role, snapshot_verifier';
    execute 'grant select on table private.snapshot_verifier_allowlist '
         || 'to snapshot_publisher_owner';

    execute $policy_select$
        create policy snapshot_verifier_allowlist_select_manager
        on private.snapshot_verifier_allowlist for select
        to snapshot_verifier_allowlist_manager using (true)
    $policy_select$;
    execute $policy_insert$
        create policy snapshot_verifier_allowlist_insert_manager
        on private.snapshot_verifier_allowlist for insert
        to snapshot_verifier_allowlist_manager with check (true)
    $policy_insert$;
    execute $policy_delete$
        create policy snapshot_verifier_allowlist_delete_manager
        on private.snapshot_verifier_allowlist for delete
        to snapshot_verifier_allowlist_manager using (true)
    $policy_delete$;

    execute $audit_table$
        create table private.snapshot_verifier_allowlist_audit (
            event_id uuid primary key default pg_catalog.gen_random_uuid(),
            occurred_at timestamptz not null default pg_catalog.clock_timestamp(),
            transaction_id bigint not null default pg_catalog.txid_current(),
            actor_session_user name not null,
            operation text not null check (operation in ('INSERT', 'DELETE')),
            user_id uuid not null,
            workspace_id uuid not null
        )
    $audit_table$;
    execute 'alter table private.snapshot_verifier_allowlist_audit enable row level security';
    execute 'alter table private.snapshot_verifier_allowlist_audit force row level security';
    execute $audit_policy$
        create policy snapshot_verifier_allowlist_audit_insert_owner
        on private.snapshot_verifier_allowlist_audit for insert
        to snapshot_verifier_audit_owner with check (true)
    $audit_policy$;
    execute $audit_select_policy$
        create policy snapshot_verifier_allowlist_audit_select_manager
        on private.snapshot_verifier_allowlist_audit for select
        to snapshot_verifier_allowlist_manager using (true)
    $audit_select_policy$;
    execute 'revoke all on table private.snapshot_verifier_allowlist_audit '
         || 'from public, anon, authenticated, service_role, snapshot_verifier, '
         || 'snapshot_publisher_owner, snapshot_verifier_allowlist_manager';
    execute 'grant select on table private.snapshot_verifier_allowlist_audit '
         || 'to snapshot_verifier_allowlist_manager';
    execute $audit_function$
        create function private.audit_snapshot_verifier_allowlist_change()
        returns trigger
        language plpgsql
        security definer
        set search_path = ''
        as $body$
        begin
            if tg_op = 'INSERT' then
                insert into private.snapshot_verifier_allowlist_audit
                    (actor_session_user, operation, user_id, workspace_id)
                values (session_user, tg_op, new.user_id, new.workspace_id);
                return new;
            elsif tg_op = 'DELETE' then
                insert into private.snapshot_verifier_allowlist_audit
                    (actor_session_user, operation, user_id, workspace_id)
                values (session_user, tg_op, old.user_id, old.workspace_id);
                return old;
            end if;
            raise exception 'unsupported allowlist audit operation'
                using errcode = '55000';
        end;
        $body$
    $audit_function$;
    execute 'revoke all on function private.audit_snapshot_verifier_allowlist_change() '
         || 'from public, anon, authenticated, service_role, snapshot_verifier, '
         || 'snapshot_publisher_owner, snapshot_verifier_allowlist_manager';
    execute $audit_trigger$
        create trigger snapshot_verifier_allowlist_audit_changes
        after insert or delete on private.snapshot_verifier_allowlist
        for each row execute function private.audit_snapshot_verifier_allowlist_change()
    $audit_trigger$;

    execute 'alter table private.snapshot_verifier_allowlist '
         || 'owner to snapshot_verifier_allowlist_owner';
    execute 'alter table private.snapshot_verifier_read_policy_baseline '
         || 'owner to snapshot_verifier_allowlist_owner';
    execute 'revoke all on table private.snapshot_verifier_read_policy_baseline '
         || 'from ' || quote_ident(current_user);
    execute 'alter table private.snapshot_verifier_allowlist_audit '
         || 'owner to snapshot_verifier_audit_owner';
    execute 'alter function private.assert_snapshot_read_policy_inventory_v2(boolean) '
         || 'owner to snapshot_verifier_allowlist_owner';
    execute 'alter function private.assert_snapshot_verifier_allowlist_admin_shape(regclass) '
         || 'owner to snapshot_verifier_allowlist_owner';
    execute 'alter function private.audit_snapshot_verifier_allowlist_change() '
         || 'owner to snapshot_verifier_audit_owner';

    execute 'revoke create on schema private from snapshot_verifier_allowlist_owner';
    execute 'revoke create on schema private from snapshot_verifier_audit_owner';

    if exists (
        select 1 from pg_catalog.pg_trigger as trigger_row
        where trigger_row.tgrelid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass
          and not trigger_row.tgisinternal
          and (trigger_row.tgname <> 'snapshot_verifier_allowlist_audit_changes'
               or trigger_row.tgfoid <>
                   'private.audit_snapshot_verifier_allowlist_change()'::pg_catalog.regprocedure
               or trigger_row.tgtype <> 13 or trigger_row.tgenabled <> 'O')
    ) or (select count(*) from pg_catalog.pg_trigger
          where tgrelid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass
            and not tgisinternal) <> 1 then
        raise exception 'allowlist audit trigger definition or enabled state is incompatible'
            using errcode = '55000';
    end if;

    -- Run the final verification while the migration actor still holds the
    -- temporary SET membership in the new owner. Step into the owner so the
    -- owner-level EXECUTE on the transferred functions is available.
    execute 'set local role snapshot_verifier_allowlist_owner';
    perform private.assert_snapshot_read_policy_inventory_v2(true);
    perform private.assert_snapshot_verifier_allowlist_admin_shape(
        'private.snapshot_verifier_allowlist'::pg_catalog.regclass
    );
    execute 'reset role';

    if not coalesce(current_is_superuser, false) then
        execute format('revoke snapshot_verifier_allowlist_owner from %I', current_user);
        execute format('revoke snapshot_verifier_audit_owner from %I', current_user);
    end if;

    if exists (
        select 1 from pg_catalog.pg_auth_members as membership
        where membership.member = current_user::pg_catalog.regrole
          and membership.roleid in (
              'snapshot_verifier_allowlist_owner'::pg_catalog.regrole,
              'snapshot_verifier_audit_owner'::pg_catalog.regrole
          )
          and not (
              -- Tolerate the ADMIN-only edge that PostgreSQL records when a
              -- non-superuser CREATEROLE actor creates the role.
              membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) or exists (
        select 1 from pg_catalog.pg_roles as api_role
        where api_role.rolname in ('anon', 'authenticated', 'service_role', 'authenticator')
          and (pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_owner', 'MEMBER')
            or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_allowlist_manager', 'MEMBER')
            or pg_catalog.pg_has_role(api_role.oid, 'snapshot_verifier_audit_owner', 'MEMBER'))
    ) then
        raise exception 'temporary migration membership remains or an API role can assume a control role'
            using errcode = '55000';
    end if;

    if not exists (
        select 1 from pg_catalog.pg_class as audit_table
        where audit_table.oid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass
          and audit_table.relowner = 'snapshot_verifier_audit_owner'::pg_catalog.regrole
          and audit_table.relrowsecurity and audit_table.relforcerowsecurity
    ) or (select count(*) from pg_catalog.pg_policy
          where polrelid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass) <> 2
       or not exists (
           select 1 from pg_catalog.pg_policy
           where polrelid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass
             and polname = 'snapshot_verifier_allowlist_audit_insert_owner'
             and polcmd = 'a' and polpermissive
             and polroles = array['snapshot_verifier_audit_owner'::pg_catalog.regrole::oid]
             and pg_catalog.pg_get_expr(polwithcheck, polrelid, true) in ('true', '(true)')
             and polqual is null
       ) or not pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'SELECT'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'INSERT'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'UPDATE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'DELETE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'TRUNCATE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'REFERENCES'
       ) or pg_catalog.has_table_privilege(
           'snapshot_verifier_allowlist_manager',
           'private.snapshot_verifier_allowlist_audit', 'TRIGGER'
       ) then
        raise exception 'allowlist audit table is not isolated and append-only for the manager'
            using errcode = '42501';
    end if;

    if not exists (
        select 1 from pg_catalog.pg_proc as audit_function
        where audit_function.oid = 'private.audit_snapshot_verifier_allowlist_change()'::pg_catalog.regprocedure
          and audit_function.proowner = 'snapshot_verifier_audit_owner'::pg_catalog.regrole
          and audit_function.prosecdef
          and audit_function.proconfig @> array['search_path=""']
    ) or exists (
        select 1 from pg_catalog.pg_proc as audit_function
        cross join lateral pg_catalog.aclexplode(
            coalesce(audit_function.proacl,
                     pg_catalog.acldefault('f', audit_function.proowner))
        ) as acl
        where audit_function.oid = 'private.audit_snapshot_verifier_allowlist_change()'::pg_catalog.regprocedure
          and (acl.grantee <> 'snapshot_verifier_audit_owner'::pg_catalog.regrole
            or acl.privilege_type <> 'EXECUTE')
    ) then
        raise exception 'audit trigger function owner, SECURITY DEFINER, search_path, or EXECUTE ACL is unsafe'
            using errcode = '42501';
    end if;

    if (select count(*) from pg_catalog.pg_policy
        where polrelid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass) <> 2
       or not exists (
           select 1 from pg_catalog.pg_policy
           where polrelid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass
             and polname = 'snapshot_verifier_allowlist_audit_select_manager'
             and polcmd = 'r' and polpermissive
             and polroles = array['snapshot_verifier_allowlist_manager'::pg_catalog.regrole::oid]
             and pg_catalog.pg_get_expr(polqual, polrelid, true) in ('true', '(true)')
             and polwithcheck is null
       ) or exists (
           select 1 from pg_catalog.pg_class as audit_table
           cross join lateral pg_catalog.aclexplode(
               coalesce(audit_table.relacl,
                        pg_catalog.acldefault('r', audit_table.relowner))
           ) as acl
           where audit_table.oid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass
             and not (
                 (acl.grantee = 'snapshot_verifier_audit_owner'::pg_catalog.regrole
                  and acl.privilege_type in ('SELECT', 'INSERT', 'UPDATE', 'DELETE',
                                             'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN'))
                 or (acl.grantee = 'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
                     and acl.privilege_type = 'SELECT' and not acl.is_grantable)
             )
       ) then
        raise exception 'allowlist audit read policy or table ACL differs from the approved contract'
            using errcode = '42501';
    end if;

    -- The ownership transfer above already replaced the migration actor's
    -- EXECUTE with the owner-level EXECUTE of snapshot_verifier_allowlist_owner,
    -- so no explicit revoke is needed here (and none would be permitted after
    -- the ownership transfer). The final ACL check below confirms the result.

    if exists (
        select 1 from pg_catalog.pg_proc as function_row
        cross join lateral pg_catalog.aclexplode(
            coalesce(function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner))
        ) as acl
        where function_row.oid in (
                'private.assert_snapshot_read_policy_inventory_v2(boolean)'::pg_catalog.regprocedure,
                'private.assert_snapshot_verifier_allowlist_admin_shape(regclass)'::pg_catalog.regprocedure
            )
          and (function_row.proowner <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
               or acl.grantee <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
               or acl.privilege_type <> 'EXECUTE')
    ) then
        raise exception 'snapshot guard function ownership or EXECUTE ACL is unsafe'
            using errcode = '42501';
    end if;

    if (select relowner from pg_catalog.pg_class
        where oid = 'private.snapshot_verifier_allowlist'::pg_catalog.regclass)
           <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
       or (select relowner from pg_catalog.pg_class
           where oid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass)
           <> 'snapshot_verifier_audit_owner'::pg_catalog.regrole
       or (select relowner from pg_catalog.pg_class
           where oid = 'private.snapshot_verifier_read_policy_baseline'::pg_catalog.regclass)
           <> 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole then
        raise exception 'snapshot control table ownership postcondition failed'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_shdepend
        where refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and refobjid = 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
          and deptype = 'o') <> 4
       or (select count(*) from pg_catalog.pg_shdepend
           where refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
             and refobjid = 'snapshot_verifier_audit_owner'::pg_catalog.regrole
             and deptype = 'o') <> 2
       or exists (
           select 1 from pg_catalog.pg_shdepend as dependency
           where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
             and dependency.deptype = 'o'
             and (
                 (dependency.refobjid = 'snapshot_verifier_allowlist_owner'::pg_catalog.regrole
                  and not (
                      (dependency.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                       and dependency.objid in (
                           'private.snapshot_verifier_allowlist'::pg_catalog.regclass,
                           'private.snapshot_verifier_read_policy_baseline'::pg_catalog.regclass
                       ))
                      or (dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
                          and dependency.objid in (
                              'private.assert_snapshot_read_policy_inventory_v2(boolean)'::pg_catalog.regprocedure,
                              'private.assert_snapshot_verifier_allowlist_admin_shape(regclass)'::pg_catalog.regprocedure
                          ))
                  ))
                 or (dependency.refobjid = 'snapshot_verifier_audit_owner'::pg_catalog.regrole
                     and not (
                         (dependency.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                          and dependency.objid = 'private.snapshot_verifier_allowlist_audit'::pg_catalog.regclass)
                         or (dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
                             and dependency.objid = 'private.audit_snapshot_verifier_allowlist_change()'::pg_catalog.regprocedure)
                     ))
                 or dependency.refobjid = 'snapshot_verifier_allowlist_manager'::pg_catalog.regrole
             )
       ) then
        raise exception 'snapshot control role object ownership differs from the approved owner set'
            using errcode = '55000';
    end if;

    -- Deliberately do not VALIDATE source_snapshots_published_verifier_binding.
    -- Legacy published rows need a separately reviewed, read-only inventory.
end;
$atomic_admin_install$;

drop table pg_temp.expected_binding;
drop table pg_temp.objects;
drop table pg_temp.source_snapshots;
