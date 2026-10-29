-- Verifier identity (design O4, amended): a dedicated Supabase Auth user on a
-- private allowlist publishes staged snapshots through two narrow RPCs.
--
--   begin_snapshot_verification  -> returns the staged claim and the identity
--                                   (id, version) of the stored Storage object.
--   publish_verified_snapshot    -> publishes only when the caller presents the
--                                   digest/size it computed from the bytes AND the
--                                   Storage object is still the one it examined.
--
-- What this does NOT do:
--   * PostgreSQL cannot recompute the SHA-256 of Storage bytes. The attestation
--     is only as honest as the verifier process; it must match the owner's staged
--     claim and every read re-verifies the bytes against that claim.
--   * Comparing the Storage object's (id, version, size) at publication detects
--     replacement made through Storage. It cannot see bytes replaced beneath
--     Storage, and it is not a lock on the object (FOR SHARE would need UPDATE on
--     storage.objects and would only lock the metadata row).
--   * Assumes storage.objects exposes "id" and "version"; the guard below makes
--     the migration fail closed if it does not. Whether "version" changes on
--     every upload must still be confirmed against a live Storage service.
--
-- The allowlist is EMPTY by default, so publication stays closed until an
-- operator inserts a (verifier user id, workspace id) pair by hand (outside the
-- API). A verifier is scoped to the workspaces listed for it: both RPCs and the
-- Storage policy require the requested/staged workspace to match an entry, and
-- an unscoped or mismatching call is refused with 42501 before any lookup, so
-- it also reveals nothing about snapshots of other workspaces. This migration
-- supersedes the direct-UPDATE publication path of the earlier migration: a
-- published row must now carry the verifier binding below.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'storage' and table_name = 'objects' and column_name = 'id'
    ) or not exists (
        select 1 from information_schema.columns
        where table_schema = 'storage' and table_name = 'objects' and column_name = 'version'
    ) then
        raise exception 'storage.objects must expose id and version for verifier binding'
            using errcode = '55000';
    end if;
end
$$;

create schema if not exists private;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'snapshot_publisher_owner') then
        execute 'create role snapshot_publisher_owner nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;
end
$$;

-- Existing roles are never normalized in place. Fail before granting privileges
-- if either identity has unsafe attributes, memberships, ownership, or ACLs.
select private.assert_snapshot_role_safe('snapshot_verifier');
select private.assert_snapshot_role_safe('snapshot_publisher_owner');

do $$
begin
    if exists (
        select 1
        from pg_catalog.pg_policies as policy
        cross join lateral unnest(policy.roles) as granted_role(role_name)
        where granted_role.role_name = 'snapshot_verifier'
          and not (
              (policy.schemaname = 'public'
               and policy.tablename = 'source_snapshots'
               and policy.policyname in (
                   'source_snapshots_select_verifier',
                   'source_snapshots_update_verifier_staged'
               ))
              or (policy.schemaname = 'storage'
                  and policy.tablename = 'objects'
                  and policy.policyname = 'source_snapshots_objects_select_verifier')
          )
    ) then
        raise exception 'snapshot_verifier has an unapproved direct row policy'
            using errcode = '55000';
    end if;
end
$$;

-- Remove only the known legacy policies; any other direct policy was rejected
-- above instead of being silently replaced.
drop policy if exists source_snapshots_select_verifier on public.source_snapshots;
drop policy if exists source_snapshots_update_verifier_staged on public.source_snapshots;
drop policy if exists source_snapshots_objects_select_verifier on storage.objects;

-- Verifier allowlist: closed by default, never reachable through the API.
-- Each entry scopes ONE Auth user to ONE workspace. workspace_id is NOT NULL, so
-- there is no "all workspaces" entry; a verifier for several workspaces needs one
-- row per workspace, added by an operator. Every RPC and policy below compares
-- the requested/staged workspace with this scope; the caller cannot choose a
-- different workspace by passing another UUID.
create table if not exists private.snapshot_verifier_allowlist (
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, workspace_id)
);

alter table private.snapshot_verifier_allowlist enable row level security;
alter table private.snapshot_verifier_allowlist force row level security;
revoke all on table private.snapshot_verifier_allowlist
    from public, anon, authenticated, service_role;

-- Verifier binding recorded at publication.
alter table public.source_snapshots
    add column if not exists storage_object_id uuid,
    add column if not exists storage_object_version text,
    add column if not exists verified_by uuid references auth.users (id);

-- NOT VALID: enforced for every new or updated row without presuming that no
-- earlier row exists in an environment this migration cannot inspect. The
-- catalog and Storage read policies below also hide legacy published rows that
-- lack any binding. Before a later VALIDATE CONSTRAINT, inspect violations
-- read-only with:
--   select workspace_id, source_id, version_id
--   from public.source_snapshots
--   where state = 'published'
--     and (verified_by is null or storage_object_id is null
--          or storage_object_version is null
--          or length(storage_object_version) not between 1 and 512);
-- Do not fabricate verifier identity or object binding, downgrade published
-- rows, or validate until a separately reviewed recovery process resolves every
-- violation without weakening the published-row immutability trigger.
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

-- Existing published rows can predate the binding constraint. Hide their
-- catalog metadata unless all three binding fields are present; the Storage
-- policy additionally matches the live object id/version before returning bytes.
alter policy source_snapshots_select_granted_published_or_own_staged
    on public.source_snapshots
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = source_snapshots.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
        and (
            (
                source_snapshots.state = 'published'
                and source_snapshots.verified_by is not null
                and source_snapshots.storage_object_id is not null
                and source_snapshots.storage_object_version is not null
                and length(source_snapshots.storage_object_version) between 1 and 512
                and exists (
                    select 1
                    from public.source_grants as source_grant
                    where source_grant.workspace_id = source_snapshots.workspace_id
                      and source_grant.source_id = source_snapshots.source_id
                      and source_grant.principal_id = (select auth.uid())
                      and source_grant.grant_type = 'read'
                )
            )
            or (
                source_snapshots.state = 'staged'
                and source_snapshots.created_by = (select auth.uid())
            )
        )
    );

alter policy source_snapshots_objects_select_granted_published
    on storage.objects
    using (
        objects.bucket_id = 'source-snapshots'
        and exists (
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
              and snapshot.storage_object_id is not null
              and snapshot.storage_object_version is not null
              and length(snapshot.storage_object_version) between 1 and 512
              and snapshot.storage_object_id = objects.id
              and snapshot.storage_object_version = objects.version
              and objects.owner_id = snapshot.created_by::text
              and (objects.metadata ->> 'size')::bigint = snapshot.size_bytes
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
              and source_grant.principal_id = (select auth.uid())
              and source_grant.grant_type = 'read'
        )
    );

-- Minimal privileges of the function owner (not an API role, cannot log in).
grant usage on schema private to snapshot_publisher_owner;
grant usage on schema public to snapshot_publisher_owner;
grant usage on schema storage to snapshot_publisher_owner;
-- PostgreSQL requires CREATE on the containing schema while transferring
-- function ownership. This temporary privilege is revoked immediately after.
grant create on schema public to snapshot_publisher_owner;
grant select on table private.snapshot_verifier_allowlist to snapshot_publisher_owner;
grant select on table public.source_snapshots to snapshot_publisher_owner;
grant update (
    state, verified_sha256, verified_size_bytes,
    verified_by, storage_object_id, storage_object_version
) on table public.source_snapshots to snapshot_publisher_owner;
grant select on table storage.objects to snapshot_publisher_owner;

-- JWT subject of the caller, read directly from the request GUC instead of
-- auth.uid(), so the SECURITY DEFINER functions do not require USAGE on the
-- auth schema (which the migration actor postgres cannot grant).
create or replace function private.verified_user_id()
returns uuid
language sql
stable
set search_path = ''
as $function$
    select coalesce(
        nullif(current_setting('request.jwt.claim.sub', true), ''),
        (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    )::uuid;
$function$;

revoke all on function private.verified_user_id()
    from public, anon, authenticated, service_role;
grant execute on function private.verified_user_id() to snapshot_publisher_owner;

create policy snapshot_verifier_allowlist_select_owner
    on private.snapshot_verifier_allowlist
    for select
    to snapshot_publisher_owner
    using (true);

create policy source_snapshots_select_publisher_owner
    on public.source_snapshots
    for select
    to snapshot_publisher_owner
    using (true);

create policy source_snapshots_update_publisher_owner
    on public.source_snapshots
    for update
    to snapshot_publisher_owner
    using (source_snapshots.state = 'staged')
    with check (source_snapshots.state = 'published');

create policy source_snapshots_objects_select_publisher_owner
    on storage.objects
    for select
    to snapshot_publisher_owner
    using (objects.bucket_id = 'source-snapshots');

-- The legacy role remains inert; the RPC owner is the only privileged database
-- identity and is never granted to an API role.

-- Defence in depth for every publication, whatever path performs the UPDATE.
create or replace function private.require_verification_binding()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if new.state = 'published' and (
        new.verified_by is null
        or new.storage_object_id is null
        or new.storage_object_version is null
        or not exists (
            select 1
            from private.snapshot_verifier_allowlist as allowed
            where allowed.user_id = new.verified_by
              and allowed.workspace_id = new.workspace_id
        )
        or not exists (
            select 1
            from storage.objects as stored
            where stored.bucket_id = 'source-snapshots'
              and stored.name = new.object_key
              and stored.id = new.storage_object_id
              and stored.version = new.storage_object_version
              and stored.owner_id = new.created_by::text
              and (stored.metadata ->> 'size')::bigint = new.size_bytes
        )
    ) then
        raise exception 'publication requires an allowlisted verifier and the verified storage object'
            using errcode = '42501';
    end if;
    return new;
end;
$function$;

revoke all on function private.require_verification_binding()
    from public, anon, authenticated, service_role;

create trigger source_snapshots_require_verification_binding
    before update on public.source_snapshots
    for each row execute function private.require_verification_binding();

-- Read-only check used by the Storage policy below: true only for a staged
-- object whose workspace is in the caller's own allowlist scope.
create or replace function public.verifier_may_read_staged_object(p_object_name text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $function$
    select exists (
        select 1
        from private.snapshot_verifier_allowlist as allowed
        join public.source_snapshots as snapshot
          on snapshot.workspace_id = allowed.workspace_id
        where allowed.user_id = private.verified_user_id()
          and snapshot.object_key = p_object_name
          and snapshot.state = 'staged'
    );
$function$;

create policy source_snapshots_objects_select_allowlisted_verifier
    on storage.objects
    for select
    to authenticated
    using (
        objects.bucket_id = 'source-snapshots'
        and public.verifier_may_read_staged_object(objects.name)
    );

create or replace function public.begin_snapshot_verification(
    p_workspace_id uuid,
    p_source_id uuid,
    p_version_id uuid
)
returns table (
    out_object_key text,
    out_claimed_sha256 text,
    out_claimed_size_bytes bigint,
    out_storage_object_id uuid,
    out_storage_object_version text
)
language plpgsql
volatile
security definer
set search_path = ''
as $function$
begin
    if private.verified_user_id() is null or not exists (
        select 1
        from private.snapshot_verifier_allowlist as allowed
        where allowed.user_id = private.verified_user_id()
          and allowed.workspace_id = p_workspace_id
    ) then
        raise exception 'caller is not an allowlisted snapshot verifier for this workspace'
            using errcode = '42501';
    end if;

    return query
    select snapshot.object_key, snapshot.sha256, snapshot.size_bytes,
           stored.id, stored.version
    from public.source_snapshots as snapshot
    join storage.objects as stored
      on stored.bucket_id = 'source-snapshots'
     and stored.name = snapshot.object_key
    where snapshot.workspace_id = p_workspace_id
      and snapshot.source_id = p_source_id
      and snapshot.version_id = p_version_id
      and snapshot.state = 'staged'
      and stored.version is not null
      and stored.owner_id = snapshot.created_by::text
      and (stored.metadata ->> 'size')::bigint = snapshot.size_bytes;

    if not found then
        raise exception 'no verifiable staged snapshot'
            using errcode = 'P0002';
    end if;
end;
$function$;

create or replace function public.publish_verified_snapshot(
    p_workspace_id uuid,
    p_source_id uuid,
    p_version_id uuid,
    p_sha256 text,
    p_size_bytes bigint,
    p_storage_object_id uuid,
    p_storage_object_version text
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $function$
declare
    staged public.source_snapshots%rowtype;
begin
    if private.verified_user_id() is null or not exists (
        select 1
        from private.snapshot_verifier_allowlist as allowed
        where allowed.user_id = private.verified_user_id()
          and allowed.workspace_id = p_workspace_id
    ) then
        raise exception 'caller is not an allowlisted snapshot verifier for this workspace'
            using errcode = '42501';
    end if;

    if p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
       or p_size_bytes is null or p_size_bytes < 0
       or p_storage_object_id is null
       or p_storage_object_version is null
       or length(p_storage_object_version) not between 1 and 512
    then
        raise exception 'invalid verification evidence' using errcode = '22023';
    end if;

    select * into staged
    from public.source_snapshots as snapshot
    where snapshot.workspace_id = p_workspace_id
      and snapshot.source_id = p_source_id
      and snapshot.version_id = p_version_id
      and snapshot.state = 'staged'
    for update;

    if not found then
        raise exception 'no verifiable staged snapshot' using errcode = 'P0002';
    end if;

    -- The triggers re-check digest, size, allowlist and the live Storage object
    -- (id, version, uploader, size) before the row can become published.
    update public.source_snapshots as snapshot
    set state = 'published',
        verified_sha256 = p_sha256,
        verified_size_bytes = p_size_bytes,
        verified_by = private.verified_user_id(),
        storage_object_id = p_storage_object_id,
        storage_object_version = p_storage_object_version
    where snapshot.workspace_id = staged.workspace_id
      and snapshot.source_id = staged.source_id
      and snapshot.version_id = staged.version_id
      and snapshot.state = 'staged';
end;
$function$;

-- The Supabase base image grants EXECUTE on public functions to anon,
-- authenticated, and service_role by default. Remove those grants while the
-- migration actor still owns the functions: an ALTER OWNER later would
-- re-assign the grantor and turn this REVOKE into a no-op. Then EXECUTE is
-- granted to the authenticated role only.
revoke all on function public.verifier_may_read_staged_object(text)
    from public, anon, authenticated, service_role;
revoke all on function public.begin_snapshot_verification(uuid, uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.publish_verified_snapshot(uuid, uuid, uuid, text, bigint, uuid, text)
    from public, anon, authenticated, service_role;
grant execute on function public.verifier_may_read_staged_object(text) to authenticated;
grant execute on function public.begin_snapshot_verification(uuid, uuid, uuid) to authenticated;
grant execute on function public.publish_verified_snapshot(uuid, uuid, uuid, text, bigint, uuid, text)
    to authenticated;

-- Function owner: ALTER OWNER needs SET on the target role. The temporary
-- membership is granted only to the migration actor, then removal is mandatory.
do $$
begin
    execute format(
        'grant snapshot_publisher_owner to %I with set true, inherit false',
        current_user
    );
end
$$;

alter function public.verifier_may_read_staged_object(text)
    owner to snapshot_publisher_owner;
alter function public.begin_snapshot_verification(uuid, uuid, uuid)
    owner to snapshot_publisher_owner;
alter function public.publish_verified_snapshot(uuid, uuid, uuid, text, bigint, uuid, text)
    owner to snapshot_publisher_owner;

do $$
begin
    execute format('revoke snapshot_publisher_owner from %I', current_user);
    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where membership.roleid = 'snapshot_publisher_owner'::pg_catalog.regrole
          and membership.member = current_user::pg_catalog.regrole
          and not (
              -- Tolerate the ADMIN-only edge that PostgreSQL records when a
              -- non-superuser CREATEROLE actor creates the role; it cannot be
              -- revoked by that actor. The temporary SET edge must be gone.
              membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) then
        raise exception 'temporary snapshot_publisher_owner membership remains after revoke'
            using errcode = '55000';
    end if;
end
$$;

revoke create on schema public from snapshot_publisher_owner;

-- Reusable fail-closed audit so SQL tests can inject an unauthorized grant and
-- prove that the same guard used during migration rejects it.
create or replace function private.assert_snapshot_verifier_function_acl_safe()
returns void
language plpgsql
set search_path = ''
as $function$
declare
    owner_role oid := 'snapshot_publisher_owner'::pg_catalog.regrole;
    authenticated_role oid := 'authenticated'::pg_catalog.regrole;
    function_count integer;
begin
    select count(*)::integer
      into function_count
    from pg_catalog.pg_proc as fn
    join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
    where namespace.nspname = 'public'
      and fn.proname in (
          'begin_snapshot_verification',
          'publish_verified_snapshot',
          'verifier_may_read_staged_object'
      );

    if function_count <> 3 or exists (
        select 1
        from pg_catalog.pg_proc as fn
        join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
        where namespace.nspname = 'public'
          and fn.proname in (
              'begin_snapshot_verification',
              'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )
          and (
              fn.proowner <> owner_role
              or not fn.prosecdef
              or not coalesce(fn.proconfig @> array['search_path=""'], false)
          )
    ) then
        raise exception 'verifier functions must be the three expected security definer functions owned by snapshot_publisher_owner with empty search_path'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_proc as fn
        join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
        cross join lateral pg_catalog.aclexplode(
            coalesce(fn.proacl, pg_catalog.acldefault('f', fn.proowner))
        ) as acl
        where namespace.nspname = 'public'
          and fn.proname in (
              'begin_snapshot_verification',
              'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )
          and acl.privilege_type = 'EXECUTE'
          and (
              acl.grantee not in (owner_role, authenticated_role)
              or (acl.grantee = authenticated_role and acl.is_grantable)
          )
    ) or exists (
        select 1
        from pg_catalog.pg_proc as fn
        join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
        where namespace.nspname = 'public'
          and fn.proname in (
              'begin_snapshot_verification',
              'publish_verified_snapshot',
              'verifier_may_read_staged_object'
          )
          and not pg_catalog.has_function_privilege(authenticated_role, fn.oid, 'EXECUTE')
    ) then
        raise exception 'verifier function EXECUTE grants differ from the authenticated-only allowlist'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_shdepend as dependency
        where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and dependency.refobjid = owner_role
          and dependency.deptype = 'o'
          and not (
              dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
              and dependency.objid in (
                  'public.verifier_may_read_staged_object(text)'::pg_catalog.regprocedure,
                  'public.begin_snapshot_verification(uuid,uuid,uuid)'::pg_catalog.regprocedure,
                  'public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)'::pg_catalog.regprocedure
              )
              and dependency.objsubid = 0
          )
    ) or (
        select count(*)
        from pg_catalog.pg_shdepend as dependency
        where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and dependency.refobjid = owner_role
          and dependency.deptype = 'o'
    ) <> 3 then
        raise exception 'snapshot_publisher_owner owns an unapproved object'
            using errcode = '55000';
    end if;
end
$function$;

revoke all on function private.assert_snapshot_verifier_function_acl_safe()
    from public, anon, authenticated, service_role;
select private.assert_snapshot_verifier_function_acl_safe();

-- Fail closed if role membership, ownership, function properties, or effective
-- EXECUTE ACLs differ from the narrow design above.
do $$
declare
    owner_role oid := 'snapshot_publisher_owner'::pg_catalog.regrole;
    verifier_role oid := 'snapshot_verifier'::pg_catalog.regrole;
    api_role text;
begin
    foreach api_role in array array['anon', 'authenticated', 'service_role', 'authenticator']
    loop
        if exists (select 1 from pg_catalog.pg_roles where rolname = api_role)
           and (
               pg_catalog.pg_has_role(api_role, owner_role, 'member')
               or pg_catalog.pg_has_role(api_role, verifier_role, 'member')
           )
        then
            raise exception 'an API role can assume a publication role'
                using errcode = '42501';
        end if;
    end loop;

    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where (membership.roleid in (owner_role, verifier_role)
               or membership.member in (owner_role, verifier_role))
          and not (
              -- Tolerate the ADMIN-only edge that PostgreSQL records when a
              -- non-superuser CREATEROLE actor creates the role.
              membership.roleid in (owner_role, verifier_role)
              and membership.member = current_user::pg_catalog.regrole
              and membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) or exists (
        select 1
        from pg_catalog.pg_roles as other_role
        where other_role.oid not in (owner_role, verifier_role)
          and other_role.oid <> current_user::pg_catalog.regrole
          and not other_role.rolsuper
          and (
              pg_catalog.pg_has_role(other_role.oid, owner_role, 'member')
              or pg_catalog.pg_has_role(other_role.oid, verifier_role, 'member')
              or pg_catalog.pg_has_role(owner_role, other_role.oid, 'member')
              or pg_catalog.pg_has_role(verifier_role, other_role.oid, 'member')
          )
    ) then
        raise exception 'a publication role has a direct or transitive membership'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_roles as role_row
        where role_row.oid = owner_role
          and (
              role_row.rolcanlogin or role_row.rolinherit or role_row.rolsuper
              or role_row.rolbypassrls or role_row.rolcreaterole
              or role_row.rolcreatedb or role_row.rolreplication
          )
    ) then
        raise exception 'snapshot_publisher_owner has unsafe role attributes'
            using errcode = '55000';
    end if;

    if not pg_catalog.has_schema_privilege(owner_role, 'public', 'USAGE')
       or not pg_catalog.has_schema_privilege(owner_role, 'private', 'USAGE')
       or not pg_catalog.has_schema_privilege(owner_role, 'storage', 'USAGE')
       or pg_catalog.has_schema_privilege(owner_role, 'public', 'CREATE')
       or not pg_catalog.has_function_privilege(owner_role, 'private.verified_user_id()', 'EXECUTE')
       or not pg_catalog.has_table_privilege(owner_role, 'private.snapshot_verifier_allowlist', 'SELECT')
       or not pg_catalog.has_table_privilege(owner_role, 'public.source_snapshots', 'SELECT')
       or not pg_catalog.has_table_privilege(owner_role, 'storage.objects', 'SELECT')
       or pg_catalog.has_table_privilege(owner_role, 'private.snapshot_verifier_allowlist', 'INSERT')
       or pg_catalog.has_table_privilege(owner_role, 'private.snapshot_verifier_allowlist', 'UPDATE')
       or pg_catalog.has_table_privilege(owner_role, 'private.snapshot_verifier_allowlist', 'DELETE')
       or pg_catalog.has_table_privilege(owner_role, 'storage.objects', 'INSERT')
       or pg_catalog.has_table_privilege(owner_role, 'storage.objects', 'UPDATE')
       or pg_catalog.has_table_privilege(owner_role, 'storage.objects', 'DELETE')
       or pg_catalog.has_table_privilege(owner_role, 'public.source_snapshots', 'INSERT')
       or pg_catalog.has_table_privilege(owner_role, 'public.source_snapshots', 'DELETE')
       or pg_catalog.has_table_privilege(owner_role, 'public.source_grants', 'SELECT')
       or pg_catalog.has_table_privilege(owner_role, 'public.workspace_memberships', 'SELECT')
       or exists (
           select 1
           from pg_catalog.pg_attribute as attribute
           where attribute.attrelid = 'public.source_snapshots'::pg_catalog.regclass
             and attribute.attnum > 0
             and not attribute.attisdropped
             and attribute.attname not in (
                 'state', 'verified_sha256', 'verified_size_bytes',
                 'verified_by', 'storage_object_id', 'storage_object_version'
             )
             and pg_catalog.has_column_privilege(
                 owner_role, attribute.attrelid, attribute.attname, 'UPDATE'
             )
       ) then
        raise exception 'snapshot_publisher_owner has missing or excessive runtime privileges'
            using errcode = '42501';
    end if;

    if not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'state', 'UPDATE')
       or not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'verified_sha256', 'UPDATE')
       or not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'verified_size_bytes', 'UPDATE')
       or not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'verified_by', 'UPDATE')
       or not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'storage_object_id', 'UPDATE')
       or not pg_catalog.has_column_privilege(owner_role, 'public.source_snapshots', 'storage_object_version', 'UPDATE')
    then
        raise exception 'snapshot_publisher_owner is missing a publication-column privilege'
            using errcode = '42501';
    end if;
end
$$;

comment on table private.snapshot_verifier_allowlist is
    'Auth users allowed to verify and publish staged snapshots, scoped per workspace. Empty by default; managed only by an operator outside the API.';
