-- Forward-only hardening for the snapshot verifier.
-- The application status of migrations 20261022100000 through 20261022120000
-- is not established by this checkout. Keep those files intact; this migration
-- validates and constrains the resulting schema after they have run.

create or replace function private.assert_snapshot_verifier_allowlist_shape(
    p_relation pg_catalog.regclass
)
returns void
language plpgsql
set search_path = ''
as $function$
declare
    relation_oid oid := p_relation::oid;
    relation_owner oid;
    user_attnum smallint;
    workspace_attnum smallint;
    added_at_attnum smallint;
    constraint_count integer;
    foreign_key_count integer;
    policy_count integer;
begin
    select relation.relowner
      into relation_owner
    from pg_catalog.pg_class as relation
    where relation.oid = relation_oid
      and relation.relkind = 'r';

    if relation_owner is null then
        raise exception 'snapshot verifier allowlist must be an ordinary table'
            using errcode = '55000';
    end if;

    select attribute.attnum
      into user_attnum
    from pg_catalog.pg_attribute as attribute
    where attribute.attrelid = relation_oid
      and attribute.attname = 'user_id'
      and attribute.atttypid = 'pg_catalog.uuid'::pg_catalog.regtype
      and attribute.attnotnull
      and not attribute.attisdropped;

    select attribute.attnum
      into workspace_attnum
    from pg_catalog.pg_attribute as attribute
    where attribute.attrelid = relation_oid
      and attribute.attname = 'workspace_id'
      and attribute.atttypid = 'pg_catalog.uuid'::pg_catalog.regtype
      and attribute.attnotnull
      and not attribute.attisdropped;

    select attribute.attnum
      into added_at_attnum
    from pg_catalog.pg_attribute as attribute
    where attribute.attrelid = relation_oid
      and attribute.attname = 'added_at'
      and attribute.atttypid = 'pg_catalog.timestamptz'::pg_catalog.regtype
      and attribute.attnotnull
      and not attribute.attisdropped;

    if (select count(*) from pg_catalog.pg_attribute
        where attrelid = relation_oid and attnum > 0 and not attisdropped) <> 3
       or exists (select 1 from pg_catalog.pg_attribute
                  where attrelid = relation_oid and attnum > 0 and attisdropped)
       or user_attnum is null
       or workspace_attnum is null
       or added_at_attnum is null
       or not exists (
           select 1 from pg_catalog.pg_attrdef as default_value
           where default_value.adrelid = relation_oid
             and default_value.adnum = added_at_attnum
             and pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid)
                 in ('now()', 'CURRENT_TIMESTAMP', 'transaction_timestamp()')
       ) then
        raise exception 'snapshot verifier allowlist has incompatible columns or defaults'
            using errcode = '55000';
    end if;

    select count(*)::integer
      into constraint_count
    from pg_catalog.pg_constraint as constraint_row
    where constraint_row.conrelid = relation_oid
      and constraint_row.contype = 'p'
      and constraint_row.conkey = array[user_attnum, workspace_attnum]::smallint[];

    if constraint_count <> 1 or (
        select count(*) from pg_catalog.pg_constraint
        where conrelid = relation_oid and contype = 'p'
    ) <> 1 then
        raise exception 'snapshot verifier allowlist has an incompatible primary key'
            using errcode = '55000';
    end if;

    select count(*)::integer
      into foreign_key_count
    from pg_catalog.pg_constraint as constraint_row
    where constraint_row.conrelid = relation_oid
      and constraint_row.contype = 'f'
      and constraint_row.convalidated
      and not constraint_row.condeferrable
      and constraint_row.confdeltype = 'c'
      and constraint_row.confupdtype = 'a'
      and (
          (constraint_row.conkey = array[user_attnum]::smallint[]
           and constraint_row.confrelid = 'auth.users'::pg_catalog.regclass
           and constraint_row.confkey = array[(
               select attnum from pg_catalog.pg_attribute
               where attrelid = 'auth.users'::pg_catalog.regclass
                 and attname = 'id' and not attisdropped
           )]::smallint[])
          or
          (constraint_row.conkey = array[workspace_attnum]::smallint[]
           and constraint_row.confrelid = 'public.workspaces'::pg_catalog.regclass
           and constraint_row.confkey = array[(
               select attnum from pg_catalog.pg_attribute
               where attrelid = 'public.workspaces'::pg_catalog.regclass
                 and attname = 'id' and not attisdropped
           )]::smallint[])
      );

    if foreign_key_count <> 2 or (
        select count(*) from pg_catalog.pg_constraint
        where conrelid = relation_oid and contype = 'f'
    ) <> 2 then
        raise exception 'snapshot verifier allowlist has incompatible foreign keys'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_constraint
        where conrelid = relation_oid) <> 3 then
        raise exception 'snapshot verifier allowlist has incompatible constraints'
            using errcode = '55000';
    end if;

    if not exists (
        select 1 from pg_catalog.pg_class as relation
        where relation.oid = relation_oid
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) then
        raise exception 'snapshot verifier allowlist must have RLS enabled and forced'
            using errcode = '55000';
    end if;

    select count(*)::integer
      into policy_count
    from pg_catalog.pg_policy as policy
    where policy.polrelid = relation_oid
      and policy.polname = 'snapshot_verifier_allowlist_select_owner'
      and policy.polcmd = 'r'
      and policy.polpermissive
      and policy.polroles = array['snapshot_publisher_owner'::pg_catalog.regrole::oid]
      and pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) in ('true', '(true)')
      and policy.polwithcheck is null;

    if policy_count <> 1 or (
        select count(*) from pg_catalog.pg_policy where polrelid = relation_oid
    ) <> 1 then
        raise exception 'snapshot verifier allowlist has incompatible row policies'
            using errcode = '55000';
    end if;

    if relation_owner in (
        'anon'::pg_catalog.regrole::oid,
        'authenticated'::pg_catalog.regrole::oid,
        'service_role'::pg_catalog.regrole::oid,
        'snapshot_publisher_owner'::pg_catalog.regrole::oid,
        'snapshot_verifier'::pg_catalog.regrole::oid
    ) then
        raise exception 'snapshot verifier allowlist has an untrusted owner'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from unnest(array['anon', 'authenticated', 'service_role',
                          'snapshot_verifier']) as api(role_name)
        where pg_catalog.has_table_privilege(api.role_name, relation_oid, 'SELECT')
           or pg_catalog.has_table_privilege(api.role_name, relation_oid, 'INSERT')
           or pg_catalog.has_table_privilege(api.role_name, relation_oid, 'UPDATE')
           or pg_catalog.has_table_privilege(api.role_name, relation_oid, 'DELETE')
    ) or exists (
        select 1
        from pg_catalog.pg_roles as role_row
        where role_row.oid not in (
            relation_owner,
            'snapshot_publisher_owner'::pg_catalog.regrole::oid
        )
          and not role_row.rolsuper
          -- The platform predefined roles inherit SELECT/INSERT/UPDATE/DELETE
          -- on every table by design; that is not an API-role exposure.
          and not pg_catalog.pg_has_role(role_row.oid, 'pg_read_all_data', 'MEMBER')
          and not pg_catalog.pg_has_role(role_row.oid, 'pg_write_all_data', 'MEMBER')
          and (
              pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'SELECT')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'INSERT')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'UPDATE')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'DELETE')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'TRUNCATE')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'REFERENCES')
              or pg_catalog.has_table_privilege(role_row.oid, relation_oid, 'TRIGGER')
          )
    ) or exists (
        select 1
        from pg_catalog.pg_class as relation
        cross join lateral pg_catalog.aclexplode(
            coalesce(relation.relacl, pg_catalog.acldefault('r', relation.relowner))
        ) as acl
        where relation.oid = relation_oid
          and acl.grantee = 0
          and acl.privilege_type in (
              'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
              'REFERENCES', 'TRIGGER'
          )
    ) or exists (
        select 1
        from pg_catalog.pg_attribute as attribute
        where attribute.attrelid = relation_oid
          and attribute.attnum > 0
          and not attribute.attisdropped
          and attribute.attacl is not null
    ) then
        raise exception 'snapshot verifier allowlist is exposed to an API role or PUBLIC'
            using errcode = '42501';
    end if;

    if not pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'SELECT'
       ) or exists (
           select 1
           from pg_catalog.pg_class as relation
           cross join lateral pg_catalog.aclexplode(
               coalesce(relation.relacl, pg_catalog.acldefault('r', relation.relowner))
           ) as acl
           where relation.oid = relation_oid
             and acl.grantee = 'snapshot_publisher_owner'::pg_catalog.regrole::oid
             and (acl.privilege_type <> 'SELECT' or acl.is_grantable)
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'INSERT'
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'UPDATE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'DELETE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'TRUNCATE'
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'REFERENCES'
       ) or pg_catalog.has_table_privilege(
           'snapshot_publisher_owner', relation_oid, 'TRIGGER'
       ) then
        raise exception 'snapshot publisher owner has incompatible allowlist privileges'
            using errcode = '42501';
    end if;
end;
$function$;

revoke all on function private.assert_snapshot_verifier_allowlist_shape(pg_catalog.regclass)
    from public, anon, authenticated, service_role;

select private.assert_snapshot_verifier_allowlist_shape(
    'private.snapshot_verifier_allowlist'::pg_catalog.regclass
);

create or replace function private.assert_snapshot_api_role_cannot_assume(
    p_api_role name,
    p_target_role name
)
returns void
language plpgsql
set search_path = ''
as $function$
declare
    api_role oid := pg_catalog.to_regrole(p_api_role::text)::oid;
    target_role oid := pg_catalog.to_regrole(p_target_role::text)::oid;
begin
    if api_role is null or target_role is null then
        return;
    end if;

    if exists (
        select 1 from pg_catalog.pg_roles
        where oid = api_role and not rolsuper
    ) and pg_catalog.pg_has_role(api_role, target_role, 'MEMBER') then
        raise exception 'an API role can assume a verifier role: % -> %',
            p_api_role, p_target_role
            using errcode = '55000';
    end if;
end;
$function$;

revoke all on function private.assert_snapshot_api_role_cannot_assume(name, name)
    from public, anon, authenticated, service_role;

do $role_preflight$
declare
    role_name text;
    role_id oid;
begin
    foreach role_name in array array['snapshot_verifier', 'snapshot_publisher_owner']
    loop
        role_id := pg_catalog.to_regrole(role_name)::oid;
        if role_id is null or exists (
            select 1 from pg_catalog.pg_roles as role_row
            where role_row.oid = role_id
              and (role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
                   or role_row.rolbypassrls or role_row.rolcreaterole
                   or role_row.rolcreatedb or role_row.rolreplication)
        ) then
            raise exception 'snapshot verifier role is missing or has unsafe attributes: %', role_name
                using errcode = '55000';
        end if;

        if exists (
            select 1 from pg_catalog.pg_auth_members as membership
            where (membership.roleid = role_id or membership.member = role_id)
              and not (
                  -- Tolerate the ADMIN-only edge that PostgreSQL records when a
                  -- non-superuser CREATEROLE actor creates the role.
                  membership.roleid = role_id
                  and membership.member = current_user::pg_catalog.regrole
                  and membership.admin_option
                  and not membership.set_option
                  and not membership.inherit_option
              )
        ) then
            raise exception 'snapshot verifier role has a direct membership edge: %', role_name
                using errcode = '55000';
        end if;
    end loop;

    foreach role_name in array array[
        'anon', 'authenticated', 'service_role', 'authenticator',
        'supabase_storage_admin'
    ]
    loop
        perform private.assert_snapshot_api_role_cannot_assume(
            role_name::pg_catalog.name, 'snapshot_verifier'
        );
        perform private.assert_snapshot_api_role_cannot_assume(
            role_name::pg_catalog.name, 'snapshot_publisher_owner'
        );
    end loop;
end;
$role_preflight$;

create or replace function private.assert_snapshot_read_policy_inventory(
    p_fences_required boolean
)
returns void
language plpgsql
set search_path = ''
as $function$
declare
    source_relation oid := 'public.source_snapshots'::pg_catalog.regclass;
    storage_relation oid := 'storage.objects'::pg_catalog.regclass;
    authenticated_role oid := 'authenticated'::pg_catalog.regrole;
    owner_role oid := 'snapshot_publisher_owner'::pg_catalog.regrole;
    source_count integer;
    storage_count integer;
begin
    if exists (
        select 1
        from pg_catalog.pg_policy as policy
        where policy.polrelid = source_relation
          and policy.polcmd in ('r', '*')
          and not (
              (policy.polname = 'source_snapshots_select_granted_published_or_own_staged'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array[authenticated_role])
              or
              (policy.polname = 'source_snapshots_select_publisher_owner'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array[owner_role])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_select_verifier_role_gate_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[0::oid])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_select_authenticated_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[authenticated_role])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_select_publisher_owner_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[owner_role])
          )
    ) or exists (
        select 1
        from pg_catalog.pg_policy as policy
        where policy.polrelid = storage_relation
          and policy.polcmd in ('r', '*')
          and not (
              (policy.polname = 'source_snapshots_objects_select_granted_published'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array[authenticated_role])
              or
              (policy.polname = 'source_snapshots_objects_select_allowlisted_verifier'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array[authenticated_role])
              or
              (policy.polname = 'source_snapshots_objects_select_publisher_owner'
               and policy.polcmd = 'r' and policy.polpermissive
               and policy.polroles = array[owner_role])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_objects_verifier_role_gate_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[0::oid])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_objects_select_authenticated_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[authenticated_role])
              or
              (p_fences_required
               and policy.polname = 'source_snapshots_objects_publisher_owner_restrictive_fence'
               and policy.polcmd = 'r' and not policy.polpermissive
               and policy.polroles = array[owner_role])
          )
    ) then
        raise exception 'unapproved SELECT or ALL policy exists on a snapshot read relation'
            using errcode = '55000';
    end if;

    select count(*)::integer into source_count
    from pg_catalog.pg_policy
    where polrelid = source_relation and polcmd in ('r', '*');
    select count(*)::integer into storage_count
    from pg_catalog.pg_policy
    where polrelid = storage_relation and polcmd in ('r', '*');

    if source_count <> (case when p_fences_required then 5 else 2 end)
       or storage_count <> (case when p_fences_required then 6 else 3 end) then
        raise exception 'snapshot read policy inventory is incomplete'
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

revoke all on function private.assert_snapshot_read_policy_inventory(boolean)
    from public, anon, authenticated, service_role;

-- Keep both policy installations in one PostgreSQL statement so a failure
-- cannot leave only one relation fenced even when the migration runner does
-- not wrap the whole file in a transaction. Unknown policies are not changed.
do $install_read_fences$
declare
    source_fence_count integer;
    storage_fence_count integer;
begin
    -- Each relation carries three role-scoped restrictive fences:
    --   * a "to public" role gate that keeps every role outside the two
    --     approved readers fail-closed, exactly like the original
    --     "to public ... else false" fence, but without referencing any table
    --     the requesting role cannot read;
    --   * one precise fence per approved reader.
    -- Role scoping (instead of a single CASE on current_user) is required
    -- because PostgreSQL checks table privileges in every CASE branch, not
    -- only in the branch that is actually taken.
    select count(*) into source_fence_count
    from pg_catalog.pg_policy
    where polrelid = 'public.source_snapshots'::pg_catalog.regclass
      and polname in (
          'source_snapshots_select_verifier_role_gate_restrictive_fence',
          'source_snapshots_select_authenticated_restrictive_fence',
          'source_snapshots_select_publisher_owner_restrictive_fence'
      );
    select count(*) into storage_fence_count
    from pg_catalog.pg_policy
    where polrelid = 'storage.objects'::pg_catalog.regclass
      and polname in (
          'source_snapshots_objects_verifier_role_gate_restrictive_fence',
          'source_snapshots_objects_select_authenticated_restrictive_fence',
          'source_snapshots_objects_publisher_owner_restrictive_fence'
      );

    if source_fence_count is distinct from storage_fence_count then
        raise exception 'snapshot read restrictive fences are only partially installed; refusing partial state'
            using errcode = '55000';
    elsif source_fence_count not in (0, 3) then
        raise exception 'snapshot read restrictive fences are only partially installed; refusing partial state'
            using errcode = '55000';
    elsif source_fence_count = 3 then
        perform private.assert_snapshot_read_policy_inventory(true);
    else
        perform private.assert_snapshot_read_policy_inventory(false);

        execute $source_role_gate_fence$
            create policy source_snapshots_select_verifier_role_gate_restrictive_fence
                on public.source_snapshots
                as restrictive
                for select
                to public
                using (
                    current_user = 'authenticated'::pg_catalog.name
                    or current_user = 'snapshot_publisher_owner'::pg_catalog.name
                )
        $source_role_gate_fence$;

        execute $source_authenticated_fence$
            create policy source_snapshots_select_authenticated_restrictive_fence
                on public.source_snapshots
                as restrictive
                for select
                to authenticated
                using (
                    (
                        source_snapshots.state = 'published'
                        and source_snapshots.verified_by is not null
                        and source_snapshots.storage_object_id is not null
                        and source_snapshots.storage_object_version is not null
                        and length(source_snapshots.storage_object_version) between 1 and 512
                        and exists (
                            select 1
                            from public.workspace_memberships as membership
                            join public.source_grants as source_grant
                              on source_grant.workspace_id = membership.workspace_id
                             and source_grant.source_id = source_snapshots.source_id
                            where membership.workspace_id = source_snapshots.workspace_id
                              and membership.user_id = (select auth.uid())
                              and membership.status = 'active'
                              and source_grant.principal_id = (select auth.uid())
                              and source_grant.grant_type = 'read'
                        )
                    )
                    or (
                        source_snapshots.state = 'staged'
                        and source_snapshots.created_by = (select auth.uid())
                        and exists (
                            select 1 from public.workspace_memberships as membership
                            where membership.workspace_id = source_snapshots.workspace_id
                              and membership.user_id = (select auth.uid())
                              and membership.status = 'active'
                        )
                    )
                )
        $source_authenticated_fence$;

        -- SECURITY NOTE - why this restrictive fence admits 'published'
        --
        -- PostgreSQL re-checks the NEW row of an UPDATE against the updating
        -- role's SELECT policies, permissive and restrictive alike. The
        -- publication path is an UPDATE staged -> published executed by the
        -- snapshot_publisher_owner identity inside
        -- public.publish_verified_snapshot, so a fence limited to
        -- state = 'staged' makes publication impossible ("new row violates
        -- row-level security policy"). Admitting both states is therefore the
        -- minimum needed for the transition, not a widening of the read rule.
        --
        -- Invariants the next maintainer must keep:
        --   * This fence is a FLOOR, never the read rule. Every read surface
        --     owned by snapshot_publisher_owner must keep its own explicit
        --     "state = 'staged'" filter. Current surfaces:
        --       - public.begin_snapshot_verification    (projection, staged only)
        --       - public.publish_verified_snapshot      (SELECT/UPDATE, staged only)
        --       - public.verifier_may_read_staged_object (boolean, staged only)
        --   * Any NEW SECURITY DEFINER function owned by
        --     snapshot_publisher_owner requires an independent security review:
        --     without its own state filter it would expose the published rows
        --     of every allowlisted workspace.
        --   * snapshot_publisher_owner must stay NOLOGIN, NOINHERIT,
        --     NOBYPASSRLS, NOCREATEROLE, NOCREATEDB and NOREPLICATION, and must
        --     never become assumable from a normal session (no SET membership).
        --     private.assert_snapshot_role_safe and the API-role preflight in
        --     this migration fail the migration if that ever changes.
        --   * The allowlist join below keeps the scope per workspace: a verifier
        --     allowlisted for workspace A never sees workspace B.
        execute $source_owner_fence$
            create policy source_snapshots_select_publisher_owner_restrictive_fence
                on public.source_snapshots
                as restrictive
                for select
                to snapshot_publisher_owner
                using (
                    source_snapshots.state in ('staged', 'published')
                    and exists (
                        select 1
                        from private.snapshot_verifier_allowlist as allowed
                        where allowed.user_id = (select auth.uid())
                          and allowed.workspace_id = source_snapshots.workspace_id
                    )
                )
        $source_owner_fence$;

        execute $storage_role_gate_fence$
            create policy source_snapshots_objects_verifier_role_gate_restrictive_fence
                on storage.objects
                as restrictive
                for select
                to public
                using (
                    objects.bucket_id is distinct from 'source-snapshots'
                    or current_user = 'authenticated'::pg_catalog.name
                    or current_user = 'snapshot_publisher_owner'::pg_catalog.name
                )
        $storage_role_gate_fence$;

        execute $storage_authenticated_fence$
            create policy source_snapshots_objects_select_authenticated_restrictive_fence
                on storage.objects
                as restrictive
                for select
                to authenticated
                using (
                    objects.bucket_id is distinct from 'source-snapshots'
                    or (
                        exists (
                            select 1
                            from public.source_snapshots as snapshot
                            join public.workspace_memberships as membership
                              on membership.workspace_id = snapshot.workspace_id
                            join public.source_grants as source_grant
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
                        )
                        or public.verifier_may_read_staged_object(objects.name)
                    )
                )
        $storage_authenticated_fence$;

        -- Unlike the catalog fence above, this one stays staged-only: the
        -- publisher owner never INSERTs or UPDATEs storage.objects, so it has
        -- no new-row re-check to satisfy. It also keeps the pre-existing rule
        -- that the owner cannot read any bucket other than source-snapshots.
        execute $storage_owner_fence$
            create policy source_snapshots_objects_publisher_owner_restrictive_fence
                on storage.objects
                as restrictive
                for select
                to snapshot_publisher_owner
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
                )
        $storage_owner_fence$;

        perform private.assert_snapshot_read_policy_inventory(true);
    end if;
end;
$install_read_fences$;

-- Reassert function ownership using a single statement-level transaction.
-- This can repair only functions still owned by the migration actor; any other
-- ownership state fails closed. Failure in GRANT, ALTER OWNER, or REVOKE rolls
-- back all temporary membership and schema privileges from this block.
do $atomic_owner_transfer$
declare
    migration_role oid := current_user::pg_catalog.regrole;
    migration_name name := current_user::pg_catalog.name;
    owner_role oid := 'snapshot_publisher_owner'::pg_catalog.regrole;
    needs_transfer boolean;
begin
    if exists (
        select 1 from pg_catalog.pg_roles
        where oid = owner_role
          and (rolcanlogin or rolinherit or rolsuper or rolbypassrls
               or rolcreaterole or rolcreatedb or rolreplication)
    ) or exists (
        select 1 from pg_catalog.pg_auth_members as membership
        where (membership.roleid = owner_role or membership.member = owner_role)
          and not (
              -- Tolerate the ADMIN-only edge that PostgreSQL records when a
              -- non-superuser CREATEROLE actor creates the role.
              membership.roleid = owner_role
              and membership.member = migration_role
              and membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) then
        raise exception 'snapshot_publisher_owner has unsafe attributes or memberships'
            using errcode = '55000';
    end if;

    if (select count(*) from pg_catalog.pg_proc as function_row
        join pg_catalog.pg_namespace as namespace on namespace.oid = function_row.pronamespace
        where namespace.nspname = 'public'
          and function_row.proname in (
              'begin_snapshot_verification', 'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )) <> 3 then
        raise exception 'the three expected verifier functions are not present exactly once'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_proc as function_row
        join pg_catalog.pg_namespace as namespace on namespace.oid = function_row.pronamespace
        where namespace.nspname = 'public'
          and function_row.proname in (
              'begin_snapshot_verification', 'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )
          and function_row.proowner not in (owner_role, migration_role)
    ) then
        raise exception 'a verifier function has an unexpected owner; refusing ownership repair'
            using errcode = '55000';
    end if;

    select exists (
        select 1
        from pg_catalog.pg_proc as function_row
        join pg_catalog.pg_namespace as namespace on namespace.oid = function_row.pronamespace
        where namespace.nspname = 'public'
          and function_row.proname in (
              'begin_snapshot_verification', 'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )
          and function_row.proowner = migration_role
    ) into needs_transfer;

    if needs_transfer then
        if exists (
            select 1
            from pg_catalog.pg_proc as function_row
            join pg_catalog.pg_namespace as namespace on namespace.oid = function_row.pronamespace
            where namespace.nspname = 'public'
              and function_row.proname in (
                  'begin_snapshot_verification', 'publish_verified_snapshot',
                  'verifier_may_read_staged_object'
              )
              and function_row.proowner = owner_role
        ) then
            raise exception 'verifier functions have mixed ownership; refusing partial transfer'
                using errcode = '55000';
        end if;
        if pg_catalog.has_schema_privilege(owner_role, 'public', 'CREATE') then
            raise exception 'snapshot_publisher_owner already has unexpected CREATE on public'
                using errcode = '55000';
        end if;
        if exists (
            select 1 from pg_catalog.pg_auth_members as membership
            where membership.roleid = owner_role and membership.member = migration_role
              and not (
                  -- Tolerate the ADMIN-only edge that PostgreSQL records when a
                  -- non-superuser CREATEROLE actor creates the role.
                  membership.admin_option
                  and not membership.set_option
                  and not membership.inherit_option
              )
        ) then
            raise exception 'migration actor already has snapshot_publisher_owner membership'
                using errcode = '55000';
        end if;

        execute 'grant create on schema public to snapshot_publisher_owner';
        execute format(
            'grant snapshot_publisher_owner to %I with set true, inherit false, admin false',
            migration_name
        );

        execute 'alter function public.verifier_may_read_staged_object(text) owner to snapshot_publisher_owner';
        execute 'alter function public.begin_snapshot_verification(uuid, uuid, uuid) owner to snapshot_publisher_owner';
        execute 'alter function public.publish_verified_snapshot(uuid, uuid, uuid, text, bigint, uuid, text) owner to snapshot_publisher_owner';

        execute 'revoke snapshot_publisher_owner from ' || pg_catalog.quote_ident(migration_name);
        execute 'revoke create on schema public from snapshot_publisher_owner';
    end if;

    if exists (
        select 1 from pg_catalog.pg_auth_members as membership
        where membership.roleid = owner_role and membership.member = migration_role
          and not (
              -- Tolerate the ADMIN-only edge that PostgreSQL records when a
              -- non-superuser CREATEROLE actor creates the role.
              membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) or pg_catalog.has_schema_privilege(owner_role, 'public', 'CREATE') then
        raise exception 'temporary snapshot publisher membership or CREATE privilege remains'
            using errcode = '55000';
    end if;
end;
$atomic_owner_transfer$;

select private.assert_snapshot_verifier_function_acl_safe();

comment on function private.assert_snapshot_verifier_allowlist_shape(pg_catalog.regclass)
    is 'Internal fail-closed schema compatibility check for the snapshot verifier allowlist.';
comment on function private.assert_snapshot_read_policy_inventory(boolean)
    is 'Internal fail-closed inventory check for snapshot SELECT and ALL policies.';
