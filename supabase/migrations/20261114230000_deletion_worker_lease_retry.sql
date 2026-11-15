-- P0.2: deletion-worker lease/claim and bounded purge retry.
--
-- The worker now claims one request atomically with a lease before processing
-- it. A crashed worker stops owning the request when its lease expires. Only
-- retryable worker failures are retried, with bounded attempts and backoff;
-- verification failures remain terminal. The single-instance advisory lock is
-- still supported, but it is no longer the only duplicate-processing guard.

alter table public.data_deletion_requests
    add column if not exists attempts integer not null default 0,
    add column if not exists last_attempt_at timestamptz,
    add column if not exists next_attempt_at timestamptz,
    add column if not exists lease_owner text,
    add column if not exists lease_expires_at timestamptz;

alter table public.data_deletion_requests
    add constraint data_deletion_requests_attempts_check
        check (attempts >= 0);

alter table public.data_deletion_requests
    add constraint data_deletion_requests_lease_owner_check
        check (
            lease_owner is null
            or pg_catalog.length(lease_owner) between 1 and 200
        );

create index data_deletion_requests_claimable_idx
    on public.data_deletion_requests (status, next_attempt_at, created_at)
    where status in ('accepted', 'purge_pending', 'purge_failed');

create or replace function private.claim_deletion_request(
    p_worker_id text,
    p_lease_seconds integer default 300,
    p_max_attempts integer default 5
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    claimed public.data_deletion_requests;
    previous_status text;
begin
    if p_worker_id is null
       or pg_catalog.length(p_worker_id) not between 1 and 200 then
        raise exception 'worker id is required' using errcode = '22023';
    end if;
    if p_lease_seconds < 30 or p_lease_seconds > 3600 then
        raise exception 'lease seconds out of range' using errcode = '22023';
    end if;
    if p_max_attempts < 1 or p_max_attempts > 20 then
        raise exception 'max attempts out of range' using errcode = '22023';
    end if;

    select * into claimed
      from public.data_deletion_requests
     where (
             status in ('accepted', 'purge_pending')
             or (
                 status = 'purge_failed'
                 and purge_error_category is distinct from 'verification_failed'
                 and attempts < p_max_attempts
                 and coalesce(next_attempt_at, created_at)
                     <= pg_catalog.clock_timestamp()
             )
           )
       and (
             lease_expires_at is null
             or lease_expires_at <= pg_catalog.clock_timestamp()
           )
     order by coalesce(next_attempt_at, created_at), created_at
     limit 1
     for update skip locked;

    if not found then
        return null;
    end if;

    previous_status := claimed.status;

    update public.data_deletion_requests
       set status = case
               when claimed.status = 'purge_failed' then 'purge_pending'
               else claimed.status
           end,
           claim_id = pg_catalog.gen_random_uuid(),
           claimed_at = pg_catalog.clock_timestamp(),
           lease_owner = p_worker_id,
           lease_expires_at = pg_catalog.clock_timestamp()
               + pg_catalog.make_interval(secs => p_lease_seconds),
           last_attempt_at = pg_catalog.clock_timestamp(),
           next_attempt_at = null,
           attempts = claimed.attempts + 1,
           purge_error_category = case
               when claimed.status = 'purge_failed' then null
               else claimed.purge_error_category
           end
     where request_id = claimed.request_id;

    return pg_catalog.jsonb_build_object(
        'request_id', claimed.request_id,
        'previous_status', previous_status,
        'attempts', claimed.attempts + 1,
        'lease_expires_at', pg_catalog.clock_timestamp()
            + pg_catalog.make_interval(secs => p_lease_seconds)
    );
end;
$function$;

create or replace function private.release_deletion_claim(
    p_request_id uuid,
    p_worker_id text,
    p_status text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    updated integer := 0;
begin
    if p_status not in ('purge_pending', 'purge_failed', 'purged') then
        raise exception 'invalid release status' using errcode = '22023';
    end if;
    update public.data_deletion_requests
       set lease_owner = null,
           lease_expires_at = null,
           status = p_status
     where request_id = p_request_id
       and lease_owner = p_worker_id;
    get diagnostics updated = row_count;
    if updated = 0 then
        return pg_catalog.jsonb_build_object(
            'status', 'stale_lease', 'applied', false
        );
    end if;
    return pg_catalog.jsonb_build_object(
        'status', p_status, 'applied', true
    );
end;
$function$;

create or replace function private.record_deletion_failure(
    p_request_id uuid,
    p_worker_id text,
    p_category text,
    p_max_attempts integer default 5
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    delay_seconds integer;
    terminal boolean;
begin
    if p_category not in ('effect_failed', 'verification_failed', 'worker_error') then
        raise exception 'invalid failure category' using errcode = '22023';
    end if;
    if p_max_attempts < 1 or p_max_attempts > 20 then
        raise exception 'max attempts out of range' using errcode = '22023';
    end if;

    select * into request_row
      from public.data_deletion_requests
     where request_id = p_request_id
     for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    if request_row.lease_owner is distinct from p_worker_id then
        return pg_catalog.jsonb_build_object(
            'status', 'stale_lease', 'applied', false
        );
    end if;
    if request_row.status = 'purged' then
        return pg_catalog.jsonb_build_object(
            'status', 'purged', 'terminal', true, 'attempts', request_row.attempts
        );
    end if;

    terminal := p_category = 'verification_failed'
        or request_row.attempts >= p_max_attempts;

    delay_seconds := case
        when request_row.attempts <= 1 then 300
        when request_row.attempts = 2 then 900
        when request_row.attempts = 3 then 3600
        else 21600
    end;

    update public.data_deletion_requests
       set status = 'purge_failed',
           purge_error_category = p_category,
           lease_owner = null,
           lease_expires_at = null,
           next_attempt_at = case
               when terminal then null
               else pg_catalog.clock_timestamp()
                   + pg_catalog.make_interval(secs => delay_seconds)
           end
     where request_id = p_request_id;

    if p_category <> 'verification_failed' then
        insert into public.evidence_deletion_audit (
            actor_user_id, workspace_id, resource_type, resource_id, action, result,
            request_id, scope_type, metadata
        ) values (
            request_row.requester_user_id, null, 'deletion_request', p_request_id,
            'purge_failed', 'failure', p_request_id, 'account',
            pg_catalog.jsonb_build_object(
                'failure_category', p_category,
                'attempts', request_row.attempts,
                'terminal', terminal
            )
        );
    end if;

    return pg_catalog.jsonb_build_object(
        'status', 'purge_failed',
        'terminal', terminal,
        'attempts', request_row.attempts,
        'next_attempt_seconds', case when terminal then null else delay_seconds end
    );
end;
$function$;

revoke all on function private.claim_deletion_request(text, integer, integer)
    from public, anon, authenticated, service_role;
revoke all on function private.release_deletion_claim(uuid, text, text)
    from public, anon, authenticated, service_role;
revoke all on function private.record_deletion_failure(uuid, text, text, integer)
    from public, anon, authenticated, service_role;

grant execute on function private.claim_deletion_request(text, integer, integer)
    to account_deletion_worker;
grant execute on function private.release_deletion_claim(uuid, text, text)
    to account_deletion_worker;
grant execute on function private.record_deletion_failure(uuid, text, text, integer)
    to account_deletion_worker;
