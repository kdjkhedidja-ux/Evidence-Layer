-- Let API-key and MCP retrieval rank the whole workspace, not only the newest
-- 100 evidence records.
--
-- read_personal_api_key_evidence feeds retrieve_evidence. Its signed gateway
-- capped p_limit at 101, so retrieval only ever scored the 100 most recent
-- records, while the signed-in web app already reads up to 1000. One call per
-- retrieval also keeps the per-key rate limit (60 calls a minute) meaningful:
-- paging 100 at a time would spend ten calls on a single retrieval.
--
-- Only the evidence bound changes (1001 = 1000 records + 1 look-ahead row for
-- the next cursor). Relationships stay at 101. Everything else in the function
-- is identical to 20261110230000_personal_api_keys.sql.
--
-- The function is owned by personal_api_key_reader. The migration role takes a
-- temporary inherited membership so it can replace the definition in place
-- (ownership and grants are kept), then gives the membership back.
grant personal_api_key_reader to current_user with inherit true, set false;

create or replace function private.authorize_personal_api_key_read(
    p_resource text,
    p_key_id uuid,
    p_workspace_id uuid,
    p_limit integer,
    p_cursor_created_at text,
    p_cursor_id uuid
)
returns void
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
    current_window timestamptz;
    current_count integer;
    next_count integer;
    locked_revoked_at timestamptz;
    locked_expires_at timestamptz;
    locked_workspace_id uuid;
    locked_scopes text[];
begin
    request_headers := coalesce(
        nullif(pg_catalog.current_setting('request.headers', true), '')::jsonb,
        '{}'::jsonb
    );
    supplied_hash := request_headers ->> 'x-evidence-api-key-hash';
    supplied_proof := request_headers ->> 'x-evidence-api-key-gateway-proof';
    supplied_timestamp := nullif(request_headers ->> 'x-evidence-api-key-gateway-timestamp', '')::bigint;
    supplied_nonce := nullif(request_headers ->> 'x-evidence-api-key-gateway-nonce', '')::uuid;

    if p_resource is null
       or p_resource not in ('evidence', 'relationships')
       or p_key_id is null
       or p_workspace_id is null
       or p_limit is null
       or p_limit < 1
       or (p_resource = 'evidence' and p_limit > 1001)
       or (p_resource = 'relationships' and p_limit > 101)
       or ((p_cursor_created_at is null) <> (p_cursor_id is null))
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

    message := pg_catalog.concat_ws(E'\n',
        'v1', p_resource, p_key_id::text, p_workspace_id::text, supplied_hash,
        supplied_timestamp::text, supplied_nonce::text, p_limit::text,
        coalesce(p_cursor_created_at, ''), coalesce(p_cursor_id::text, '')
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
    select id, user_id, workspace_id, key_hash, scopes, expires_at, revoked_at,
           rate_window_started_at, rate_window_count
      into key_row
      from public.personal_api_keys
     where id = p_key_id and key_hash = supplied_hash;
    if not found
       or key_row.revoked_at is not null
       or key_row.expires_at <= pg_catalog.clock_timestamp()
       or key_row.workspace_id <> p_workspace_id
       or (p_resource = 'evidence' and not ('evidence:read' = any(key_row.scopes)))
       or (p_resource = 'relationships' and not ('relationships:read' = any(key_row.scopes))) then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;

    perform pg_catalog.set_config('app.personal_api_key_user_id', key_row.user_id::text, true);
    perform pg_catalog.set_config('app.personal_api_key_workspace_id', key_row.workspace_id::text, true);
    perform pg_catalog.set_config('app.personal_api_key_scope_evidence',
        case when 'evidence:read' = any(key_row.scopes) then 'true' else 'false' end, true);
    perform pg_catalog.set_config('app.personal_api_key_scope_relationships',
        case when 'relationships:read' = any(key_row.scopes) then 'true' else 'false' end, true);

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

    select rate_window_started_at, rate_window_count,
           revoked_at, expires_at, workspace_id, scopes
      into current_window, current_count,
           locked_revoked_at, locked_expires_at, locked_workspace_id, locked_scopes
      from public.personal_api_keys
     where id = p_key_id and key_hash = supplied_hash
     for update;
    if not found
       or locked_revoked_at is not null
       or locked_expires_at <= pg_catalog.clock_timestamp()
       or locked_workspace_id <> p_workspace_id
       or (p_resource = 'evidence' and not ('evidence:read' = any(locked_scopes)))
       or (p_resource = 'relationships' and not ('relationships:read' = any(locked_scopes))) then
        raise exception 'personal API key authorization failed' using errcode = '28000';
    end if;
    if current_window <= pg_catalog.clock_timestamp() - interval '1 minute' then
        next_count := 1;
        update public.personal_api_keys
           set rate_window_started_at = pg_catalog.clock_timestamp(),
               rate_window_count = next_count,
               last_used_at = pg_catalog.clock_timestamp()
         where id = p_key_id and key_hash = supplied_hash;
    else
        if current_count >= 60 then
            raise exception 'personal API key rate limit exceeded' using errcode = 'P4290';
        end if;
        next_count := current_count + 1;
        update public.personal_api_keys
           set rate_window_count = next_count,
               last_used_at = pg_catalog.clock_timestamp()
         where id = p_key_id and key_hash = supplied_hash;
    end if;
end;
$function$;

revoke personal_api_key_reader from current_user;
