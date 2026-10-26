-- Durable catalog of immutable source snapshot versions and per-source read
-- grants. PostgreSQL is the single source of truth for which object keys are
-- published; Storage policies (next migration) consult it.
-- Workspace membership never implies read access to a source: a row in
-- source_grants is required. User requests use the verified user token; the
-- service_role holds no grant on either table.
--
-- Publication is a separate, trusted step. Clients (role authenticated) can
-- only STAGE a version. The later verifier-identity migration exposes only
-- allowlist-scoped RPCs; this legacy role is deliberately left without table
-- grants or a direct publication policy.

create schema if not exists private;

-- A role used by the verifier must be an isolated, non-login, non-inheriting
-- principal with no memberships, object ownership, or pre-existing ACLs. A
-- NOLOGIN role can still be assumed through SET ROLE when another role is a
-- member, so check the whole membership graph as well as pg_auth_members.
create or replace function private.assert_snapshot_role_safe(p_role_name name)
returns void
language plpgsql
set search_path = ''
as $function$
declare
    target_role oid;
    option_columns integer;
begin
    select role_row.oid
      into target_role
    from pg_catalog.pg_roles as role_row
    where role_row.rolname = p_role_name;

    if target_role is null then
        raise exception 'snapshot verifier role does not exist'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_roles as role_row
        where role_row.oid = target_role
          and (
              role_row.rolcanlogin
              or role_row.rolinherit
              or role_row.rolsuper
              or role_row.rolbypassrls
              or role_row.rolcreaterole
              or role_row.rolcreatedb
              or role_row.rolreplication
          )
    ) then
        raise exception 'snapshot verifier role has unsafe role attributes'
            using errcode = '55000';
    end if;

    select count(*)::integer
      into option_columns
    from pg_catalog.pg_attribute
    where attrelid = 'pg_catalog.pg_auth_members'::pg_catalog.regclass
      and attname in ('set_option', 'inherit_option', 'admin_option')
      and not attisdropped;

    if current_setting('server_version_num')::integer < 160000
       or option_columns <> 3 then
        raise exception 'snapshot verifier role audit requires PostgreSQL 16 or newer membership options'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where (membership.roleid = target_role or membership.member = target_role)
          and (membership.set_option or membership.inherit_option or membership.admin_option)
          and not (
              -- PostgreSQL automatically records an ADMIN-only membership for a
              -- non-superuser CREATEROLE actor running CREATE ROLE. That edge
              -- cannot be revoked by the actor and does not allow SET ROLE, so
              -- it is tolerated. Any other enabled option is rejected.
              membership.roleid = target_role
              and membership.member = current_user::pg_catalog.regrole
              and membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) then
        raise exception 'snapshot verifier role has an enabled SET, INHERIT, or ADMIN membership option'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where (membership.roleid = target_role or membership.member = target_role)
          and not (
              membership.roleid = target_role
              and membership.member = current_user::pg_catalog.regrole
              and membership.admin_option
              and not membership.set_option
              and not membership.inherit_option
          )
    ) or exists (
        select 1
        from pg_catalog.pg_roles as other_role
        where other_role.oid <> target_role
          and other_role.oid <> current_user::pg_catalog.regrole
          and not other_role.rolsuper
          and (
              pg_catalog.pg_has_role(other_role.oid, target_role, 'MEMBER')
              or pg_catalog.pg_has_role(target_role, other_role.oid, 'MEMBER')
          )
    ) then
        raise exception 'snapshot verifier role has direct or transitive role memberships'
            using errcode = '55000';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_shdepend as dependency
        where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and dependency.refobjid = target_role
          and dependency.deptype in ('o', 'a')
    ) then
        raise exception 'snapshot verifier role owns objects or has pre-existing object privileges'
            using errcode = '55000';
    end if;
end;
$function$;

revoke all on function private.assert_snapshot_role_safe(name)
    from public, anon, authenticated, service_role;

do $$
begin
    if not exists (select 1 from pg_catalog.pg_roles where rolname = 'snapshot_verifier') then
        create role snapshot_verifier
            nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication;
    end if;
end
$$;

select private.assert_snapshot_role_safe('snapshot_verifier');

create table public.source_snapshots (
    workspace_id uuid not null references public.workspaces (id) on delete restrict,
    source_id uuid not null,
    version_id uuid not null,
    object_key text not null,
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint not null check (size_bytes between 0 and 8388608),
    state text not null check (state in ('staged', 'published')),
    created_by uuid not null default auth.uid() references auth.users (id),
    created_at timestamptz not null default now(),
    published_at timestamptz,
    verified_sha256 text check (verified_sha256 ~ '^[0-9a-f]{64}$'),
    verified_size_bytes bigint check (verified_size_bytes >= 0),
    verified_at timestamptz,
    primary key (workspace_id, source_id, version_id),
    constraint source_snapshots_object_key_derived check (
        object_key = 'workspace/' || workspace_id || '/source/' || source_id
            || '/version/' || version_id
    ),
    constraint source_snapshots_published_requires_verification check (
        (state = 'published') = (published_at is not null)
        and (state = 'published') = (verified_at is not null)
        and (state = 'published') = (verified_sha256 is not null)
        and (state = 'published') = (verified_size_bytes is not null)
        and (
            state <> 'published'
            or (verified_sha256 = sha256 and verified_size_bytes = size_bytes)
        )
    )
);

create unique index source_snapshots_object_key_key
    on public.source_snapshots (object_key);

create table public.source_grants (
    workspace_id uuid not null,
    source_id uuid not null,
    principal_id uuid not null,
    grant_type text not null check (grant_type = 'read'),
    granted_by uuid not null default auth.uid() references auth.users (id),
    created_at timestamptz not null default now(),
    primary key (workspace_id, source_id, principal_id, grant_type),
    -- A grant can only target someone who is (or was) a workspace member; the
    -- read path still requires the membership to be active at read time.
    constraint source_grants_member_fkey
        foreign key (workspace_id, principal_id)
        references public.workspace_memberships (workspace_id, user_id)
        on delete cascade
);

create index source_grants_principal_idx
    on public.source_grants (principal_id, workspace_id, source_id);

alter table public.source_snapshots enable row level security;
alter table public.source_snapshots force row level security;
alter table public.source_grants enable row level security;
alter table public.source_grants force row level security;

revoke all on table public.source_snapshots, public.source_grants
    from public, anon, authenticated, service_role;

-- Clients may only stage: published_at and every verified_* column are set by
-- trigger/the verifier, never supplied by authenticated. No UPDATE or DELETE.
grant select on table public.source_snapshots to authenticated;
grant insert (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes,
    state, created_by
) on table public.source_snapshots to authenticated;

grant select on table public.source_grants to authenticated;
grant insert (workspace_id, source_id, principal_id, grant_type, granted_by)
    on table public.source_grants to authenticated;
grant delete on table public.source_grants to authenticated;

-- Catalog metadata (source/version ids, digest, size) is itself access
-- controlled: a published row is visible only to an active member who holds a
-- read grant for that source. Ownership implies no access. A creator may also
-- see their own staged row so they can finish publishing it.
create policy source_snapshots_select_granted_published_or_own_staged
    on public.source_snapshots
    for select
    to authenticated
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

create policy source_snapshots_insert_active_owners
    on public.source_snapshots
    for insert
    to authenticated
    with check (
        source_snapshots.state = 'staged'
        and source_snapshots.created_by = (select auth.uid())
        and exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = source_snapshots.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

-- No UPDATE or DELETE policy exists for authenticated: a client can never
-- publish, alter, or remove a cataloged version.

create policy source_grants_select_own_or_owner
    on public.source_grants
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = source_grants.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
        and (
            source_grants.principal_id = (select auth.uid())
            or exists (
                select 1
                from public.workspace_memberships as owner_membership
                where owner_membership.workspace_id = source_grants.workspace_id
                  and owner_membership.user_id = (select auth.uid())
                  and owner_membership.role = 'owner'
                  and owner_membership.status = 'active'
            )
        )
    );

create policy source_grants_insert_active_owners
    on public.source_grants
    for insert
    to authenticated
    with check (
        source_grants.granted_by = (select auth.uid())
        and exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = source_grants.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy source_grants_delete_active_owners
    on public.source_grants
    for delete
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = source_grants.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

-- Defence in depth independent of grants and RLS (it also binds the table
-- owner). Rows must start staged with no verification data; the only allowed
-- change is staged -> published carrying a verification that matches the
-- staged digest and size. Publication timestamps are always set here.
create or replace function private.protect_source_snapshot()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'INSERT' then
        if new.state <> 'staged'
           or new.published_at is not null
           or new.verified_at is not null
           or new.verified_sha256 is not null
           or new.verified_size_bytes is not null
        then
            raise exception 'source snapshots must be staged before verification'
                using errcode = '42501';
        end if;
        return new;
    end if;

    if old.state <> 'staged'
       or new.state <> 'published'
       or new.workspace_id is distinct from old.workspace_id
       or new.source_id is distinct from old.source_id
       or new.version_id is distinct from old.version_id
       or new.object_key is distinct from old.object_key
       or new.sha256 is distinct from old.sha256
       or new.size_bytes is distinct from old.size_bytes
       or new.created_by is distinct from old.created_by
       or new.created_at is distinct from old.created_at
    then
        raise exception 'source snapshot versions are immutable once cataloged'
            using errcode = '42501';
    end if;

    if new.verified_sha256 is distinct from old.sha256
       or new.verified_size_bytes is distinct from old.size_bytes
    then
        raise exception 'publication requires a matching verified digest and size'
            using errcode = '42501';
    end if;

    new.published_at := now();
    new.verified_at := now();
    return new;
end;
$function$;

create or replace function private.reject_source_snapshot_delete()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception 'source snapshot catalog rows cannot be deleted'
        using errcode = '42501';
end;
$function$;

revoke all on function private.protect_source_snapshot()
    from public, anon, authenticated, service_role;
revoke all on function private.reject_source_snapshot_delete()
    from public, anon, authenticated, service_role;

create trigger source_snapshots_protect_immutability
    before insert or update on public.source_snapshots
    for each row execute function private.protect_source_snapshot();

create trigger source_snapshots_reject_delete
    before delete on public.source_snapshots
    for each row execute function private.reject_source_snapshot_delete();

-- The legacy role is closed by default. A signed JWT carrying
-- role=snapshot_verifier is not byte-level evidence, and the role has no direct
-- table grants or policy. The identity migration supplies the scoped path.
do $$
declare
    verifier_role oid := 'snapshot_verifier'::pg_catalog.regrole;
begin
    if exists (
        select 1
        from pg_catalog.pg_roles as other_role
        where other_role.oid <> verifier_role
          and other_role.oid <> current_user::pg_catalog.regrole
          and not other_role.rolsuper
          and (
              pg_catalog.pg_has_role(other_role.oid, verifier_role, 'MEMBER')
              or pg_catalog.pg_has_role(verifier_role, other_role.oid, 'MEMBER')
          )
    ) then
        raise exception 'snapshot_verifier has an unexpected role membership'
            using errcode = '55000';
    end if;
end
$$;

comment on table public.source_snapshots is
    'Immutable catalog of snapshot versions. Clients stage; only allowlist-scoped verifier RPCs publish with digest, size and Storage-object binding. Published rows are visible only to active members holding a read grant.';
comment on table public.source_grants is
    'Explicit per-source read grants. Workspace membership alone never grants source access.';
