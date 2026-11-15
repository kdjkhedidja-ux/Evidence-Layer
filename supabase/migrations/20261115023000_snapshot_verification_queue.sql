-- Multi-tenant snapshot publication queue.
--
-- Staging enqueues a verification job automatically. A private verifier worker
-- claims jobs with a lease and is auto-authorized for the job workspace through
-- the existing allowlist manager. The worker then uses the existing allowlisted
-- publication RPCs. No per-workspace operator allowlist setup is required.
-- Publication remains closed until a worker is registered and run.

create table if not exists private.snapshot_verification_worker (
    user_id uuid primary key references auth.users (id) on delete cascade,
    enabled boolean not null default true,
    created_at timestamptz not null default now()
);

alter table private.snapshot_verification_worker enable row level security;
alter table private.snapshot_verification_worker force row level security;
revoke all on table private.snapshot_verification_worker
    from public, anon, authenticated, service_role;

create table if not exists private.snapshot_verification_jobs (
    job_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    source_id uuid not null,
    version_id uuid not null,
    state text not null default 'queued'
        check (state in ('queued', 'claimed', 'retryable', 'published', 'dead')),
    attempts integer not null default 0 check (attempts >= 0),
    lease_owner text,
    lease_user_id uuid references auth.users (id) on delete set null,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz,
    error_category text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, source_id, version_id)
);

create index snapshot_verification_jobs_claimable_idx
    on private.snapshot_verification_jobs (state, next_attempt_at, created_at)
    where state in ('queued', 'retryable');

create index snapshot_verification_jobs_workspace_idx
    on private.snapshot_verification_jobs (workspace_id, state);

alter table private.snapshot_verification_jobs enable row level security;
alter table private.snapshot_verification_jobs force row level security;
revoke all on table private.snapshot_verification_jobs
    from public, anon, authenticated, service_role;

-- The allowlist manager owns the only INSERT path into the existing verifier
-- allowlist. The queue worker is auto-authorized for the claimed workspace.
create or replace function private.add_snapshot_verifier_allowlist(
    p_user_id uuid,
    p_workspace_id uuid
)
returns void
language sql
security definer
set search_path = ''
as $function$
    insert into private.snapshot_verifier_allowlist (user_id, workspace_id)
    values (p_user_id, p_workspace_id)
    on conflict (user_id, workspace_id) do nothing;
$function$;

create or replace function public.claim_snapshot_verification(
    p_worker_id text,
    p_lease_seconds integer default 300
)
returns table (
    out_job_id uuid,
    out_workspace_id uuid,
    out_source_id uuid,
    out_version_id uuid,
    out_attempts integer
)
language plpgsql
security definer
set search_path = ''
as $function$
declare
    worker uuid := private.verified_user_id();
    claimed private.snapshot_verification_jobs;
begin
    if worker is null or not exists (
        select 1
        from private.snapshot_verification_worker
        where user_id = worker
          and enabled
    ) then
        raise exception 'caller is not an enabled snapshot verifier worker'
            using errcode = '42501';
    end if;
    if p_worker_id is null
       or pg_catalog.length(p_worker_id) not between 1 and 200
       or p_lease_seconds < 30
       or p_lease_seconds > 3600 then
        raise exception 'invalid claim parameters' using errcode = '22023';
    end if;

    select * into claimed
    from private.snapshot_verification_jobs
    where state in ('queued', 'retryable')
      and coalesce(next_attempt_at, created_at) <= pg_catalog.clock_timestamp()
      and (lease_expires_at is null or lease_expires_at <= pg_catalog.clock_timestamp())
    order by coalesce(next_attempt_at, created_at), created_at
    limit 1
    for update skip locked;

    if not found then
        return;
    end if;

    perform private.add_snapshot_verifier_allowlist(
        worker, claimed.workspace_id
    );

    update private.snapshot_verification_jobs
       set state = 'claimed',
           attempts = claimed.attempts + 1,
           lease_owner = p_worker_id,
           lease_user_id = worker,
           lease_expires_at = pg_catalog.clock_timestamp()
               + pg_catalog.make_interval(secs => p_lease_seconds),
           next_attempt_at = null,
           updated_at = pg_catalog.clock_timestamp()
     where job_id = claimed.job_id;

    return query
    select claimed.job_id, claimed.workspace_id, claimed.source_id,
           claimed.version_id, claimed.attempts + 1;
end;
$function$;

create or replace function public.complete_snapshot_verification(
    p_job_id uuid,
    p_worker_id text
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    worker uuid := private.verified_user_id();
    job private.snapshot_verification_jobs;
begin
    if worker is null or not exists (
        select 1
        from private.snapshot_verification_worker
        where user_id = worker
          and enabled
    ) then
        raise exception 'caller is not an enabled snapshot verifier worker'
            using errcode = '42501';
    end if;
    select * into job
    from private.snapshot_verification_jobs
    where job_id = p_job_id
    for update;
    if not found then
        raise exception 'verification job not found' using errcode = 'P0002';
    end if;
    if job.lease_owner is distinct from p_worker_id then
        return;
    end if;
    if job.state = 'published' then
        return;
    end if;
    if not exists (
        select 1
        from public.source_snapshots
        where workspace_id = job.workspace_id
          and source_id = job.source_id
          and version_id = job.version_id
          and state = 'published'
    ) then
        raise exception 'snapshot is not published' using errcode = '55000';
    end if;
    update private.snapshot_verification_jobs
       set state = 'published',
           lease_owner = null,
           lease_user_id = null,
           lease_expires_at = null,
           next_attempt_at = null,
           updated_at = pg_catalog.clock_timestamp()
     where job_id = p_job_id;
end;
$function$;

create or replace function public.fail_snapshot_verification(
    p_job_id uuid,
    p_worker_id text,
    p_category text
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    worker uuid := private.verified_user_id();
    job private.snapshot_verification_jobs;
    delay_seconds integer;
    terminal boolean;
begin
    if worker is null or not exists (
        select 1
        from private.snapshot_verification_worker
        where user_id = worker
          and enabled
    ) then
        raise exception 'caller is not an enabled snapshot verifier worker'
            using errcode = '42501';
    end if;
    if p_category not in (
        'verification_failed', 'storage_error', 'transient_error', 'worker_error'
    ) then
        raise exception 'invalid failure category' using errcode = '22023';
    end if;
    select * into job
    from private.snapshot_verification_jobs
    where job_id = p_job_id
    for update;
    if not found then
        raise exception 'verification job not found' using errcode = 'P0002';
    end if;
    if job.lease_owner is distinct from p_worker_id then
        return;
    end if;
    if job.state in ('published', 'dead') then
        return;
    end if;

    terminal := p_category = 'verification_failed' or job.attempts >= 5;
    delay_seconds := case
        when job.attempts <= 1 then 60
        when job.attempts = 2 then 300
        when job.attempts = 3 then 900
        else 3600
    end;

    update private.snapshot_verification_jobs
       set state = case when terminal then 'dead' else 'retryable' end,
           error_category = p_category,
           lease_owner = null,
           lease_user_id = null,
           lease_expires_at = null,
           next_attempt_at = case
               when terminal then null
               else pg_catalog.clock_timestamp()
                   + pg_catalog.make_interval(secs => delay_seconds)
           end,
           updated_at = pg_catalog.clock_timestamp()
     where job_id = p_job_id;
end;
$function$;

create or replace function private.enqueue_snapshot_verification_after_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := private.verified_user_id();
    active_jobs integer;
begin
    if new.state = 'staged'
       and actor is not null
       and actor = new.created_by then
        select count(*)::integer into active_jobs
        from private.snapshot_verification_jobs
        where workspace_id = new.workspace_id
          and state in ('queued', 'retryable', 'claimed');
        if active_jobs >= 50 then
            raise exception 'snapshot verification queue is full'
                using errcode = 'P0001';
        end if;
        insert into private.snapshot_verification_jobs (
            workspace_id, source_id, version_id
        ) values (
            new.workspace_id, new.source_id, new.version_id
        )
        on conflict (workspace_id, source_id, version_id) do nothing;
    end if;
    return new;
end;
$function$;

revoke all on function private.enqueue_snapshot_verification_after_insert()
    from public, anon, authenticated, service_role;
revoke all on function private.add_snapshot_verifier_allowlist(uuid, uuid)
    from public, anon, authenticated, service_role;

drop trigger if exists source_snapshots_enqueue_verification on public.source_snapshots;
create trigger source_snapshots_enqueue_verification
    after insert on public.source_snapshots
    for each row execute function private.enqueue_snapshot_verification_after_insert();

revoke all on function public.claim_snapshot_verification(text, integer)
    from public, anon, service_role;
revoke all on function public.complete_snapshot_verification(uuid, text)
    from public, anon, service_role;
revoke all on function public.fail_snapshot_verification(uuid, text, text)
    from public, anon, service_role;

grant execute on function public.claim_snapshot_verification(text, integer)
    to authenticated, snapshot_publisher_owner;
grant execute on function public.complete_snapshot_verification(uuid, text)
    to authenticated, snapshot_publisher_owner;
grant execute on function public.fail_snapshot_verification(uuid, text, text)
    to authenticated, snapshot_publisher_owner;
grant execute on function private.add_snapshot_verifier_allowlist(uuid, uuid)
    to snapshot_publisher_owner;

grant select, insert, update on private.snapshot_verification_jobs
    to snapshot_publisher_owner;
grant select on private.snapshot_verification_worker
    to snapshot_publisher_owner;

create policy snapshot_verification_jobs_publisher_owner
    on private.snapshot_verification_jobs
    for all
    to snapshot_publisher_owner
    using (true)
    with check (true);

create policy snapshot_verification_worker_publisher_owner_select
    on private.snapshot_verification_worker
    for select
    to snapshot_publisher_owner
    using (true);

-- ALTER OWNER needs temporary SET on the target role and CREATE on the
-- containing schema for the new owner. Snapshot roles have no standing
-- membership; these grants are removed in the same migration.
grant create on schema public to snapshot_publisher_owner;
grant create on schema private to snapshot_publisher_owner;
grant create on schema private to snapshot_verifier_allowlist_manager;

do $$
begin
    execute format(
        'grant snapshot_publisher_owner to %I with set true, inherit false',
        current_user
    );
    execute format(
        'grant snapshot_verifier_allowlist_manager to %I with set true, inherit false',
        current_user
    );
end
$$;

alter function public.claim_snapshot_verification(text, integer)
    owner to snapshot_publisher_owner;
alter function public.complete_snapshot_verification(uuid, text)
    owner to snapshot_publisher_owner;
alter function public.fail_snapshot_verification(uuid, text, text)
    owner to snapshot_publisher_owner;
alter function private.enqueue_snapshot_verification_after_insert()
    owner to snapshot_publisher_owner;
alter function private.add_snapshot_verifier_allowlist(uuid, uuid)
    owner to snapshot_verifier_allowlist_manager;

revoke create on schema public from snapshot_publisher_owner;
revoke create on schema private from snapshot_publisher_owner;
revoke create on schema private from snapshot_verifier_allowlist_manager;

do $$
begin
    execute format('revoke snapshot_publisher_owner from %I', current_user);
    execute format('revoke snapshot_verifier_allowlist_manager from %I', current_user);
    if exists (
        select 1
        from pg_catalog.pg_auth_members as membership
        where membership.roleid = 'snapshot_publisher_owner'::pg_catalog.regrole
          and membership.member = current_user::pg_catalog.regrole
          and membership.set_option
    ) then
        raise exception 'temporary snapshot_publisher_owner SET membership remains'
            using errcode = '55000';
    end if;
end
$$;

-- Extend the verifier function/ownership guard for the queue functions.
-- The original three publication functions remain unchanged; the queue adds
-- three public worker RPCs and one trigger function owned by the same role.
create or replace function private.assert_snapshot_verifier_function_acl_safe()
returns void
language plpgsql
set search_path = ''
as $function$
declare
    owner_role oid := 'snapshot_publisher_owner'::pg_catalog.regrole;
    authenticated_role oid := 'authenticated'::pg_catalog.regrole;
    verifier_count integer;
    queue_count integer;
    trigger_oid oid := 'private.enqueue_snapshot_verification_after_insert()'::pg_catalog.regprocedure;
begin
    select count(*)::integer into verifier_count
    from pg_catalog.pg_proc as fn
    join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
    where namespace.nspname = 'public'
      and fn.proname in (
          'begin_snapshot_verification',
          'publish_verified_snapshot',
          'verifier_may_read_staged_object'
      );
    select count(*)::integer into queue_count
    from pg_catalog.pg_proc as fn
    join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
    where (
        namespace.nspname = 'public'
        and fn.proname in (
            'claim_snapshot_verification',
            'complete_snapshot_verification',
            'fail_snapshot_verification'
        )
    ) or fn.oid = trigger_oid;

    if verifier_count <> 3 or queue_count <> 4 then
        raise exception 'verifier functions must be the three expected security definer functions owned by snapshot_publisher_owner with empty search_path'
            using errcode = '55000';
    end if;

    if exists (
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
    ) or exists (
        select 1
        from pg_catalog.pg_proc as fn
        join pg_catalog.pg_namespace as namespace on namespace.oid = fn.pronamespace
        where (
            (
                namespace.nspname = 'public'
                and fn.proname in (
                    'claim_snapshot_verification',
                    'complete_snapshot_verification',
                    'fail_snapshot_verification'
                )
            )
            or fn.oid = trigger_oid
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
        where (
            namespace.nspname = 'public'
            and fn.proname in (
                'begin_snapshot_verification',
                'publish_verified_snapshot',
                'verifier_may_read_staged_object',
                'claim_snapshot_verification',
                'complete_snapshot_verification',
                'fail_snapshot_verification'
            )
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
        where (
            namespace.nspname = 'public'
            and fn.proname in (
                'begin_snapshot_verification',
                'publish_verified_snapshot',
                'verifier_may_read_staged_object',
                'claim_snapshot_verification',
                'complete_snapshot_verification',
                'fail_snapshot_verification'
            )
        )
          and not pg_catalog.has_function_privilege(authenticated_role, fn.oid, 'EXECUTE')
    ) then
        raise exception 'verifier function EXECUTE grants differ from the authenticated-only allowlist'
            using errcode = '55000';
    end if;

    if pg_catalog.has_function_privilege(authenticated_role, trigger_oid, 'EXECUTE') then
        raise exception 'queue trigger function must not be API-executable'
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
                  'public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)'::pg_catalog.regprocedure,
                  'public.claim_snapshot_verification(text,integer)'::pg_catalog.regprocedure,
                  'public.complete_snapshot_verification(uuid,text)'::pg_catalog.regprocedure,
                  'public.fail_snapshot_verification(uuid,text,text)'::pg_catalog.regprocedure,
                  'private.enqueue_snapshot_verification_after_insert()'::pg_catalog.regprocedure
              )
              and dependency.objsubid = 0
          )
    ) or (
        select count(*)
        from pg_catalog.pg_shdepend as dependency
        where dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          and dependency.refobjid = owner_role
          and dependency.deptype = 'o'
    ) <> 7 then
        raise exception 'snapshot_publisher_owner owns an unapproved object'
            using errcode = '55000';
    end if;
end;
$function$;

revoke all on function private.assert_snapshot_verifier_function_acl_safe()
    from public, anon, authenticated, service_role;
select private.assert_snapshot_verifier_function_acl_safe();

comment on table private.snapshot_verification_jobs is
    'Queue for multi-tenant snapshot verification; workers are auto-allowlisted per workspace at claim time.';
