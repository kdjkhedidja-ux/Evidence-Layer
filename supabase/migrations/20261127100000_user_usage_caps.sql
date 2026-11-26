-- Per-user usage caps for retrieval and JEV scoring.
--
-- Public launch guard: one account must not be able to exhaust the service or
-- the JEV budget for everyone else. Counters are kept per user (not per key or
-- per workspace) in fixed UTC windows (one minute, one day). A refused call is
-- not counted. The caps themselves are supplied by the Evidence Service from
-- its environment; on the API-key path they are bound into the same HMAC
-- gateway proof as the signed reads, so only the service can choose them.
--
-- 1. private.user_usage_counters: one row per (user, kind, window kind).
-- 2. private.consume_usage: atomic check-and-count, owned by the NOLOGIN,
--    NOBYPASSRLS role usage_counter_writer.
-- 3. public.consume_user_usage: signed-in web path (caller's own JWT).
-- 4. public.consume_personal_api_key_usage: API-key/MCP path, verified like
--    private.authorize_personal_api_key_read but without spending the per-key
--    read rate limit.

do $roles$
begin
    if not exists (select 1 from pg_catalog.pg_roles where rolname = 'usage_counter_writer') then
        execute 'create role usage_counter_writer nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;
end
$roles$;

select private.assert_snapshot_role_safe('usage_counter_writer');

create table private.user_usage_counters (
    user_id uuid not null references auth.users (id) on delete cascade,
    kind text not null check (kind in ('retrieval', 'jev')),
    window_kind text not null check (window_kind in ('minute', 'day')),
    window_started_at timestamptz not null,
    used integer not null default 0 check (used >= 0),
    primary key (user_id, kind, window_kind)
);

alter table private.user_usage_counters enable row level security;
alter table private.user_usage_counters force row level security;
revoke all on table private.user_usage_counters
    from public, anon, authenticated, service_role,
         personal_api_key_manager, personal_api_key_reader, usage_counter_writer;
grant usage on schema private to usage_counter_writer;
grant select, insert, update on private.user_usage_counters to usage_counter_writer;

create policy user_usage_counters_writer_select
    on private.user_usage_counters for select to usage_counter_writer using (true);
create policy user_usage_counters_writer_insert
    on private.user_usage_counters for insert to usage_counter_writer with check (true);
create policy user_usage_counters_writer_update
    on private.user_usage_counters for update to usage_counter_writer
    using (true) with check (true);

create function private.consume_usage(
    p_user_id uuid,
    p_kind text,
    p_per_minute integer,
    p_per_day integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
declare
    now_at timestamptz := pg_catalog.clock_timestamp();
    minute_start timestamptz := pg_catalog.date_trunc('minute', now_at);
    day_start timestamptz := pg_catalog.date_trunc('day', now_at at time zone 'UTC') at time zone 'UTC';
    minute_window timestamptz;
    minute_used integer;
    day_window timestamptz;
    day_used integer;
begin
    -- A cap of 0 means "no cap for this window".
    if p_user_id is null
       or p_kind is null
       or p_kind not in ('retrieval', 'jev')
       or p_per_minute is null or p_per_minute < 0 or p_per_minute > 1000000
       or p_per_day is null or p_per_day < 0 or p_per_day > 100000000 then
        raise exception 'invalid usage request' using errcode = '22023';
    end if;

    insert into private.user_usage_counters (user_id, kind, window_kind, window_started_at, used)
    values (p_user_id, p_kind, 'minute', minute_start, 0),
           (p_user_id, p_kind, 'day', day_start, 0)
    on conflict (user_id, kind, window_kind) do nothing;

    -- Lock in a fixed order (minute, then day) so concurrent calls serialize.
    select window_started_at, used into minute_window, minute_used
      from private.user_usage_counters
     where user_id = p_user_id and kind = p_kind and window_kind = 'minute'
     for update;
    select window_started_at, used into day_window, day_used
      from private.user_usage_counters
     where user_id = p_user_id and kind = p_kind and window_kind = 'day'
     for update;
    if minute_window is null or day_window is null then
        raise exception 'usage counter is unavailable' using errcode = '55000';
    end if;

    if minute_window < minute_start then
        minute_used := 0;
    end if;
    if day_window < day_start then
        day_used := 0;
    end if;

    if p_per_minute > 0 and minute_used >= p_per_minute then
        return pg_catalog.jsonb_build_object(
            'allowed', false,
            'window', 'minute',
            'retry_after_seconds', greatest(1, pg_catalog.ceil(
                extract(epoch from (minute_start + interval '1 minute' - now_at))
            )::integer)
        );
    end if;
    if p_per_day > 0 and day_used >= p_per_day then
        return pg_catalog.jsonb_build_object(
            'allowed', false,
            'window', 'day',
            'retry_after_seconds', greatest(1, pg_catalog.ceil(
                extract(epoch from (day_start + interval '1 day' - now_at))
            )::integer)
        );
    end if;

    update private.user_usage_counters
       set window_started_at = minute_start, used = minute_used + 1
     where user_id = p_user_id and kind = p_kind and window_kind = 'minute';
    update private.user_usage_counters
       set window_started_at = day_start, used = day_used + 1
     where user_id = p_user_id and kind = p_kind and window_kind = 'day';

    return pg_catalog.jsonb_build_object(
        'allowed', true,
        'window', null,
        'retry_after_seconds', 0
    );
end;
$function$;

create function public.consume_user_usage(
    p_kind text,
    p_per_minute integer,
    p_per_day integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    claims jsonb := nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb;
    subject uuid;
begin
    if claims is null or claims ->> 'role' is distinct from 'authenticated' then
        raise exception 'authentication is required' using errcode = '42501';
    end if;
    begin
        subject := (claims ->> 'sub')::uuid;
    exception when invalid_text_representation then
        subject := null;
    end;
    if subject is null then
        raise exception 'authentication is required' using errcode = '42501';
    end if;
    -- A caller can only count against their own account.
    return private.consume_usage(subject, p_kind, p_per_minute, p_per_day);
end;
$function$;

create function public.consume_personal_api_key_usage(
    p_key_id uuid,
    p_workspace_id uuid,
    p_kind text,
    p_per_minute integer,
    p_per_day integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
declare
    request_headers jsonb;
    supplied_hash text;
    supplied_proof text;
    supplied_timestamp bigint;
    supplied_nonce uuid;
    gateway_secret bytea;
    message text;
    expected_proof text;
    consumed_nonce uuid;
    key_row record;
begin
    request_headers := coalesce(
        nullif(pg_catalog.current_setting('request.headers', true), '')::jsonb,
        '{}'::jsonb
    );
    supplied_hash := request_headers ->> 'x-evidence-api-key-hash';
    supplied_proof := request_headers ->> 'x-evidence-api-key-gateway-proof';
    supplied_timestamp := nullif(request_headers ->> 'x-evidence-api-key-gateway-timestamp', '')::bigint;
    supplied_nonce := nullif(request_headers ->> 'x-evidence-api-key-gateway-nonce', '')::uuid;

    if p_key_id is null
       or p_workspace_id is null
       or p_kind is null
       or p_kind not in ('retrieval', 'jev')
       or p_per_minute is null
       or p_per_day is null
       or supplied_hash is null
       or supplied_hash !~ '^[0-9a-f]{64}$'
       or supplied_proof is null
       or supplied_proof !~ '^[0-9a-f]{64}$'
       or supplied_timestamp is null
       or supplied_nonce is null
       or pg_catalog.abs(extract(epoch from pg_catalog.clock_timestamp())::bigint - supplied_timestamp) > 30 then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    select secret into gateway_secret
      from private.personal_api_key_gateway_secrets
     where singleton;
    if gateway_secret is null then
        raise exception 'personal API key gateway is not configured' using errcode = '55000';
    end if;

    -- A distinct message version keeps a usage proof from ever being replayed
    -- as a read proof (and the reverse); the caps are part of the proof.
    message := pg_catalog.concat_ws(E'\n',
        'v1-usage', p_kind, p_key_id::text, p_workspace_id::text, supplied_hash,
        supplied_timestamp::text, supplied_nonce::text,
        p_per_minute::text, p_per_day::text
    );
    expected_proof := pg_catalog.encode(
        extensions.hmac(
            pg_catalog.convert_to(message, 'UTF8'),
            gateway_secret,
            'sha256'
        ),
        'hex'
    );
    if expected_proof <> supplied_proof then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    delete from private.personal_api_key_gateway_nonces
     where expires_at <= pg_catalog.clock_timestamp();
    insert into private.personal_api_key_gateway_nonces (nonce, expires_at)
    values (supplied_nonce, pg_catalog.clock_timestamp() + interval '2 minutes')
    on conflict (nonce) do nothing
    returning nonce into consumed_nonce;
    if consumed_nonce is null then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    perform pg_catalog.set_config('app.personal_api_key_id', p_key_id::text, true);
    perform pg_catalog.set_config('app.personal_api_key_hash', supplied_hash, true);
    select id, user_id, workspace_id, scopes, expires_at, revoked_at
      into key_row
      from public.personal_api_keys
     where id = p_key_id and key_hash = supplied_hash;
    if not found
       or key_row.revoked_at is not null
       or key_row.expires_at <= pg_catalog.clock_timestamp()
       or key_row.workspace_id <> p_workspace_id
       or not ('evidence:read' = any(key_row.scopes)) then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    perform pg_catalog.set_config('app.personal_api_key_user_id', key_row.user_id::text, true);
    perform pg_catalog.set_config('app.personal_api_key_workspace_id', key_row.workspace_id::text, true);

    if not exists (
        select 1 from public.account_lifecycle as lifecycle
        where lifecycle.subject_id = key_row.user_id and lifecycle.state = 'available'
    ) or not exists (
        select 1 from public.workspace_memberships as membership
        where membership.user_id = key_row.user_id
          and membership.workspace_id = key_row.workspace_id
          and membership.status = 'active'
    ) then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    return private.consume_usage(key_row.user_id, p_kind, p_per_minute, p_per_day);
end;
$function$;

revoke all on function private.consume_usage(uuid,text,integer,integer)
    from public, anon, authenticated, service_role;
grant execute on function private.consume_usage(uuid,text,integer,integer)
    to usage_counter_writer, personal_api_key_reader;

revoke all on function public.consume_user_usage(text,integer,integer)
    from public, anon, authenticated, service_role;
grant execute on function public.consume_user_usage(text,integer,integer)
    to authenticated;

revoke all on function public.consume_personal_api_key_usage(uuid,uuid,text,integer,integer)
    from public, authenticated, service_role;
grant execute on function public.consume_personal_api_key_usage(uuid,uuid,text,integer,integer)
    to anon;

-- ALTER OWNER requires CREATE on the schema for the new owner; both grants are
-- revoked again below, as in 20261110230000_personal_api_keys.sql.
grant create on schema public, private to usage_counter_writer, personal_api_key_reader;

do $owners$
declare
    actor name := current_user;
begin
    execute pg_catalog.format(
        'grant usage_counter_writer to %I with set true, inherit false', actor
    );
    execute 'alter function private.consume_usage(uuid,text,integer,integer) owner to usage_counter_writer';
    execute 'alter function public.consume_user_usage(text,integer,integer) owner to usage_counter_writer';
    execute pg_catalog.format('revoke usage_counter_writer from %I', actor);

    execute pg_catalog.format(
        'grant personal_api_key_reader to %I with set true, inherit false', actor
    );
    execute 'alter function public.consume_personal_api_key_usage(uuid,uuid,text,integer,integer) owner to personal_api_key_reader';
    execute pg_catalog.format('revoke personal_api_key_reader from %I', actor);
end
$owners$;

revoke create on schema public, private from usage_counter_writer, personal_api_key_reader;

comment on table private.user_usage_counters is
    'Per-user retrieval and JEV usage in fixed UTC minute/day windows. Counts only; no request content.';
comment on role usage_counter_writer is
    'NOLOGIN, NOINHERIT, NOBYPASSRLS definer role limited to atomic per-user usage counting.';
