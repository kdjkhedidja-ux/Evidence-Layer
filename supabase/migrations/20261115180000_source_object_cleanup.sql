-- Automatic cleanup for stale staged source objects.
--
-- Catalog rows remain immutable. A stale staged version is marked abandoned
-- (hidden from the index and excluded from quotas), queued for Storage-object
-- deletion, and the object bytes are removed by the privileged deletion worker.
-- Published versions are never abandoned.

alter table public.source_snapshots
    add column if not exists abandoned_at timestamptz,
    add column if not exists object_deleted_at timestamptz;

create index source_snapshots_cleanup_idx
    on public.source_snapshots (abandoned_at, object_deleted_at, created_at)
    where abandoned_at is null;

create table if not exists private.source_object_cleanup_queue (
    queue_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    source_id uuid not null,
    version_id uuid not null unique,
    object_key text not null,
    state text not null default 'queued'
        check (state in ('queued', 'claimed', 'retryable', 'deleted', 'dead')),
    attempts integer not null default 0 check (attempts >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz,
    error_category text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index source_object_cleanup_queue_claimable_idx
    on private.source_object_cleanup_queue (state, next_attempt_at, created_at)
    where state in ('queued', 'retryable');

alter table private.source_object_cleanup_queue enable row level security;
alter table private.source_object_cleanup_queue force row level security;
revoke all on table private.source_object_cleanup_queue
    from public, anon, authenticated, service_role;

-- Allow only the cleanup-owned abandoned/deleted markers on staged rows.
-- The immutable fields, state, and publication evidence must remain unchanged.
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
           or new.abandoned_at is not null
           or new.object_deleted_at is not null
        then
            raise exception 'source snapshots must be staged before verification'
                using errcode = '42501';
        end if;
        return new;
    end if;

    if old.state = 'staged'
       and new.state = 'staged'
       and new.abandoned_at is not null
       and (old.abandoned_at is null or new.abandoned_at = old.abandoned_at)
       and (
           new.object_deleted_at is null
           or old.object_deleted_at is null
           or new.object_deleted_at = old.object_deleted_at
       )
       and new.workspace_id = old.workspace_id
       and new.source_id = old.source_id
       and new.version_id = old.version_id
       and new.object_key = old.object_key
       and new.sha256 = old.sha256
       and new.size_bytes = old.size_bytes
       and new.created_by = old.created_by
       and new.created_at = old.created_at
       and new.published_at is not distinct from old.published_at
       and new.verified_at is not distinct from old.verified_at
       and new.verified_sha256 is not distinct from old.verified_sha256
       and new.verified_size_bytes is not distinct from old.verified_size_bytes
       and new.verified_by is not distinct from old.verified_by
       and new.storage_object_id is not distinct from old.storage_object_id
       and new.storage_object_version is not distinct from old.storage_object_version
    then
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

create or replace function private.enqueue_stale_staged_sources(
    p_age_days integer,
    p_limit integer
)
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
    enqueued integer;
begin
    if p_age_days is null or p_age_days < 1 or p_age_days > 365
       or p_limit is null or p_limit < 1 or p_limit > 1000 then
        raise exception 'invalid cleanup bounds' using errcode = '22023';
    end if;

    with candidates as (
        select snapshot.workspace_id, snapshot.source_id,
               snapshot.version_id, snapshot.object_key
        from public.source_snapshots as snapshot
        where snapshot.state = 'staged'
          and snapshot.abandoned_at is null
          and snapshot.created_at < pg_catalog.clock_timestamp()
              - pg_catalog.make_interval(days => p_age_days)
          and not exists (
              select 1
              from private.snapshot_verification_jobs as job
              where job.workspace_id = snapshot.workspace_id
                and job.source_id = snapshot.source_id
                and job.version_id = snapshot.version_id
                and job.state in ('queued', 'retryable', 'claimed')
          )
        order by snapshot.created_at
        limit p_limit
        for update skip locked
    ), abandoned as (
        update public.source_snapshots as snapshot
           set abandoned_at = pg_catalog.clock_timestamp()
          from candidates
         where snapshot.workspace_id = candidates.workspace_id
           and snapshot.source_id = candidates.source_id
           and snapshot.version_id = candidates.version_id
        returning candidates.workspace_id, candidates.source_id,
                  candidates.version_id, candidates.object_key
    )
    insert into private.source_object_cleanup_queue (
        workspace_id, source_id, version_id, object_key
    )
    select workspace_id, source_id, version_id, object_key
    from abandoned
    on conflict (version_id) do nothing;
    get diagnostics enqueued = row_count;
    return enqueued;
end;
$function$;

create or replace function private.claim_source_cleanup(
    p_worker_id text,
    p_lease_seconds integer default 300
)
returns table (
    out_queue_id uuid,
    out_object_key text,
    out_attempts integer
)
language plpgsql
security definer
set search_path = ''
as $function$
declare
    claimed private.source_object_cleanup_queue;
begin
    if p_worker_id is null
       or pg_catalog.length(p_worker_id) not between 1 and 200
       or p_lease_seconds < 30 or p_lease_seconds > 3600 then
        raise exception 'invalid cleanup claim' using errcode = '22023';
    end if;
    select * into claimed
    from private.source_object_cleanup_queue
    where state in ('queued', 'retryable')
      and coalesce(next_attempt_at, created_at) <= pg_catalog.clock_timestamp()
      and (lease_expires_at is null or lease_expires_at <= pg_catalog.clock_timestamp())
    order by coalesce(next_attempt_at, created_at), created_at
    limit 1
    for update skip locked;
    if not found then
        return;
    end if;
    update private.source_object_cleanup_queue
       set state = 'claimed',
           attempts = claimed.attempts + 1,
           lease_owner = p_worker_id,
           lease_expires_at = pg_catalog.clock_timestamp()
               + pg_catalog.make_interval(secs => p_lease_seconds),
           next_attempt_at = null,
           updated_at = pg_catalog.clock_timestamp()
     where queue_id = claimed.queue_id;
    return query select claimed.queue_id, claimed.object_key, claimed.attempts + 1;
end;
$function$;

create or replace function private.complete_source_cleanup(
    p_queue_id uuid,
    p_worker_id text
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    job private.source_object_cleanup_queue;
begin
    select * into job
    from private.source_object_cleanup_queue
    where queue_id = p_queue_id
    for update;
    if not found or job.lease_owner is distinct from p_worker_id then
        return;
    end if;
    update private.source_object_cleanup_queue
       set state = 'deleted',
           lease_owner = null,
           lease_expires_at = null,
           next_attempt_at = null,
           updated_at = pg_catalog.clock_timestamp()
     where queue_id = p_queue_id;
    update public.source_snapshots
       set object_deleted_at = pg_catalog.clock_timestamp()
     where workspace_id = job.workspace_id
       and source_id = job.source_id
       and version_id = job.version_id;
end;
$function$;

create or replace function private.fail_source_cleanup(
    p_queue_id uuid,
    p_worker_id text,
    p_category text
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    job private.source_object_cleanup_queue;
    terminal boolean;
begin
    if p_category not in ('storage_error', 'worker_error') then
        raise exception 'invalid cleanup category' using errcode = '22023';
    end if;
    select * into job
    from private.source_object_cleanup_queue
    where queue_id = p_queue_id
    for update;
    if not found or job.lease_owner is distinct from p_worker_id then
        return;
    end if;
    terminal := job.attempts >= 5;
    update private.source_object_cleanup_queue
       set state = case when terminal then 'dead' else 'retryable' end,
           error_category = p_category,
           lease_owner = null,
           lease_expires_at = null,
           next_attempt_at = case
               when terminal then null
               else pg_catalog.clock_timestamp()
                   + pg_catalog.make_interval(secs => 3600)
           end,
           updated_at = pg_catalog.clock_timestamp()
     where queue_id = p_queue_id;
end;
$function$;

create or replace function public.workspace_source_usage(
    p_workspace_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    version_count integer;
    total_bytes bigint;
    staged_count integer;
    pending_invitations integer;
begin
    if actor is null or not exists (
        select 1 from public.workspace_memberships
        where workspace_id = p_workspace_id
          and user_id = actor
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'caller is not an active workspace owner'
            using errcode = '42501';
    end if;
    select count(*)::integer,
           coalesce(sum(snapshot.size_bytes), 0)::bigint,
           count(*) filter (where snapshot.state = 'staged')::integer
      into version_count, total_bytes, staged_count
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = p_workspace_id
       and snapshot.abandoned_at is null;
    select count(*)::integer into pending_invitations
      from private.workspace_invitations as invitation
     where invitation.workspace_id = p_workspace_id
       and invitation.accepted_at is null
       and invitation.revoked_at is null
       and invitation.expires_at > pg_catalog.clock_timestamp();
    return pg_catalog.jsonb_build_object(
        'version_count', version_count,
        'total_bytes', total_bytes,
        'staged_count', staged_count,
        'pending_invitations', pending_invitations
    );
end;
$function$;

revoke all on function private.enqueue_stale_staged_sources(integer, integer)
    from public, anon, authenticated, service_role;
revoke all on function private.claim_source_cleanup(text, integer)
    from public, anon, authenticated, service_role;
revoke all on function private.complete_source_cleanup(uuid, text)
    from public, anon, authenticated, service_role;
revoke all on function private.fail_source_cleanup(uuid, text, text)
    from public, anon, authenticated, service_role;
