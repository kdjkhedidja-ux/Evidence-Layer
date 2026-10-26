-- Private Storage bucket for immutable source snapshot objects. Object access
-- is derived from the catalog (public.source_snapshots), never from the object
-- path alone:
--   * INSERT: only into a key already staged by the same active workspace owner.
--   * SELECT: only a published catalog row for that exact key, an active
--     workspace member, and an explicit read grant for the source. The
--     allowlist-scoped verifier identity receives a separate staged-only policy
--     in the verifier-identity migration.
--   * No UPDATE or DELETE policy exists, so overwrite (x-upsert) and removal
--     through the Storage API are denied for every API role.
-- Roles that bypass RLS (service_role keys, database owners) are outside this
-- guarantee; the read path re-verifies size and SHA-256 against the catalog.

do $$
begin
    if to_regclass('storage.objects') is null or to_regclass('storage.buckets') is null then
        raise exception 'Supabase Storage must be enabled before this migration'
            using errcode = '55000';
    end if;
end
$$;

-- Idempotent creation that never rewrites an existing bucket. The helper is
-- invoker-rights and private to the migration owner; a same-id bucket must
-- already match every setting this migration relies on, including its name.
create or replace function private.ensure_snapshot_bucket(
    p_id text,
    p_name text,
    p_public boolean,
    p_file_size_limit bigint,
    p_allowed_mime_types text[]
)
returns void
language plpgsql
set search_path = ''
as $function$
declare
    existing_name text;
    existing_public boolean;
    existing_file_size_limit bigint;
    existing_allowed_mime_types text[];
begin
    if exists (
        select 1
        from storage.buckets as bucket
        where bucket.name = p_name
          and bucket.id <> p_id
    ) then
        raise exception 'snapshot bucket name is already used by another bucket'
            using errcode = '55000';
    end if;

    begin
        insert into storage.buckets (
            id, name, public, file_size_limit, allowed_mime_types
        ) values (
            p_id, p_name, p_public, p_file_size_limit, p_allowed_mime_types
        )
        on conflict (id) do nothing;
    exception
        when unique_violation then
            raise exception 'snapshot bucket name is already used by another bucket'
                using errcode = '55000';
    end;

    select bucket.name, bucket.public, bucket.file_size_limit,
           bucket.allowed_mime_types
      into existing_name, existing_public, existing_file_size_limit,
           existing_allowed_mime_types
    from storage.buckets as bucket
    where bucket.id = p_id;

    if not found then
        raise exception 'snapshot bucket could not be created or found'
            using errcode = '55000';
    end if;

    if existing_name is distinct from p_name
       or existing_public is distinct from p_public
       or existing_file_size_limit is distinct from p_file_size_limit
       or existing_allowed_mime_types is distinct from p_allowed_mime_types
    then
        raise exception 'snapshot bucket has conflicting name or security settings'
            using errcode = '55000';
    end if;
end;
$function$;

revoke all on function private.ensure_snapshot_bucket(text, text, boolean, bigint, text[])
    from public, anon, authenticated, service_role;

select private.ensure_snapshot_bucket(
    'source-snapshots', 'source-snapshots', false, 8388608,
    array['application/octet-stream']::text[]
);

create policy source_snapshots_objects_insert_staged_by_owner
    on storage.objects
    for insert
    to authenticated
    with check (
        objects.bucket_id = 'source-snapshots'
        and exists (
            select 1
            from public.source_snapshots as snapshot
            join public.workspace_memberships as membership
              on membership.workspace_id = snapshot.workspace_id
            where snapshot.object_key = objects.name
              and snapshot.state = 'staged'
              and snapshot.created_by = (select auth.uid())
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy source_snapshots_objects_select_granted_published
    on storage.objects
    for select
    to authenticated
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
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
              and source_grant.principal_id = (select auth.uid())
              and source_grant.grant_type = 'read'
        )
    );

-- Publication additionally requires the object to exist, to have been uploaded
-- by the staging user, and to have exactly the declared size. This does not
-- prove the content digest; that is the verifier's attested check.
create or replace function private.require_stored_snapshot_object()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if new.state = 'published' and not exists (
        select 1
        from storage.objects as stored
        where stored.bucket_id = 'source-snapshots'
          and stored.name = new.object_key
          and stored.owner_id = new.created_by::text
          and (stored.metadata ->> 'size')::bigint = new.size_bytes
    ) then
        raise exception 'publication requires the uploaded object with the declared size'
            using errcode = '42501';
    end if;
    return new;
end;
$function$;

revoke all on function private.require_stored_snapshot_object()
    from public, anon, authenticated, service_role;

create trigger source_snapshots_require_stored_object
    before update on public.source_snapshots
    for each row execute function private.require_stored_snapshot_object();
