-- V1 personal API keys are workspace-scoped read credentials only. The
-- Evidence Service signs a per-request gateway proof after checking the
-- external recovery fence; a public Data API caller cannot invoke the read
-- RPCs with a personal key alone.

do $roles$
begin
    if not exists (select 1 from pg_catalog.pg_roles where rolname = 'personal_api_key_manager') then
        execute 'create role personal_api_key_manager nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;
    if not exists (select 1 from pg_catalog.pg_roles where rolname = 'personal_api_key_reader') then
        execute 'create role personal_api_key_reader nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole noreplication';
    end if;
end
$roles$;

select private.assert_snapshot_role_safe('personal_api_key_manager');
select private.assert_snapshot_role_safe('personal_api_key_reader');


create function private.personal_api_key_subject()
returns uuid
language sql
stable
set search_path = ''
as $function$
    select coalesce(
        nullif(pg_catalog.current_setting('request.jwt.claim.sub', true), ''),
        (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    )::uuid
$function$;
revoke all on function private.personal_api_key_subject()
    from public, anon, authenticated, service_role;
grant execute on function private.personal_api_key_subject()
    to personal_api_key_manager, personal_api_key_reader;

create table public.personal_api_keys (
    id uuid primary key,
    user_id uuid not null references auth.users (id) on delete cascade,
    workspace_id uuid not null,
    name text not null check (char_length(btrim(name)) between 1 and 80),
    key_prefix text not null check (key_prefix ~ '^evl_pk_[0-9a-f]{8}$'),
    key_hash text not null check (key_hash ~ '^[0-9a-f]{64}$'),
    scopes text[] not null default array['evidence:read', 'relationships:read']::text[]
        check (scopes = array['evidence:read', 'relationships:read']::text[]),
    created_at timestamptz not null default pg_catalog.statement_timestamp(),
    expires_at timestamptz not null,
    revoked_at timestamptz,
    last_used_at timestamptz,
    rate_window_started_at timestamptz not null default pg_catalog.statement_timestamp(),
    rate_window_count integer not null default 0 check (rate_window_count between 0 and 60),
    constraint personal_api_keys_workspace_member_fkey
        foreign key (workspace_id, user_id)
        references public.workspace_memberships (workspace_id, user_id)
        on delete cascade,
    constraint personal_api_keys_expiration_check
        check (expires_at > created_at and expires_at <= created_at + interval '90 days')
);

create unique index personal_api_keys_key_hash_key
    on public.personal_api_keys (key_hash);
create index personal_api_keys_user_active_idx
    on public.personal_api_keys (user_id, expires_at)
    where revoked_at is null;
create index personal_api_keys_workspace_idx
    on public.personal_api_keys (workspace_id, user_id);

alter table public.personal_api_keys enable row level security;
alter table public.personal_api_keys force row level security;
revoke all on table public.personal_api_keys
    from public, anon, authenticated, service_role, personal_api_key_manager, personal_api_key_reader;

grant select (id, user_id, workspace_id, name, key_prefix, scopes, created_at, expires_at, revoked_at, last_used_at)
    on public.personal_api_keys to authenticated, personal_api_key_manager;
grant insert (id, user_id, workspace_id, name, key_prefix, key_hash, scopes, expires_at)
    on public.personal_api_keys to personal_api_key_manager;
grant update (revoked_at)
    on public.personal_api_keys to personal_api_key_manager;
grant select (id, user_id, workspace_id, scopes, key_hash, expires_at, revoked_at, rate_window_started_at, rate_window_count)
    on public.personal_api_keys to personal_api_key_reader;
grant update (last_used_at, rate_window_started_at, rate_window_count)
    on public.personal_api_keys to personal_api_key_reader;

grant usage on schema public, private to personal_api_key_manager;
grant select (user_id, workspace_id, role, status)
    on public.workspace_memberships to personal_api_key_manager;
grant select (subject_id, state)
    on public.account_lifecycle to personal_api_key_manager;

grant usage on schema public, private, extensions to personal_api_key_reader;
grant select (user_id, workspace_id, status)
    on public.workspace_memberships to personal_api_key_reader;
grant select (subject_id, state)
    on public.account_lifecycle to personal_api_key_reader;
grant select (id, workspace_id, content, source_label, source_id, source_version_id, created_at, updated_at)
    on public.evidence_records to personal_api_key_reader;
grant select (id, workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note, created_by, created_at, updated_by, updated_at)
    on public.evidence_relationships to personal_api_key_reader;

create policy personal_api_keys_select_own
    on public.personal_api_keys
    for select to authenticated
    using (user_id = (select private.personal_api_key_subject()));

create policy personal_api_keys_manager_select_own
    on public.personal_api_keys
    for select to personal_api_key_manager
    using (user_id = (select private.personal_api_key_subject()));

create policy personal_api_keys_manager_insert_own
    on public.personal_api_keys
    for insert to personal_api_key_manager
    with check (
        user_id = (select private.personal_api_key_subject())
        and exists (
            select 1 from public.workspace_memberships as membership
            where membership.user_id = (select private.personal_api_key_subject())
              and membership.workspace_id = personal_api_keys.workspace_id
              and membership.status = 'active'
        )
    );

create policy personal_api_keys_manager_revoke_own
    on public.personal_api_keys
    for update to personal_api_key_manager
    using (user_id = (select private.personal_api_key_subject()))
    with check (user_id = (select private.personal_api_key_subject()) and revoked_at is not null);

create policy personal_api_keys_reader_select_exact
    on public.personal_api_keys
    for select to personal_api_key_reader
    using (
        id = nullif(pg_catalog.current_setting('app.personal_api_key_id', true), '')::uuid
        and key_hash = nullif(pg_catalog.current_setting('app.personal_api_key_hash', true), '')
    );

create policy personal_api_keys_reader_update_exact
    on public.personal_api_keys
    for update to personal_api_key_reader
    using (
        id = nullif(pg_catalog.current_setting('app.personal_api_key_id', true), '')::uuid
        and key_hash = nullif(pg_catalog.current_setting('app.personal_api_key_hash', true), '')
        and user_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
    )
    with check (
        id = nullif(pg_catalog.current_setting('app.personal_api_key_id', true), '')::uuid
        and key_hash = nullif(pg_catalog.current_setting('app.personal_api_key_hash', true), '')
        and user_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
    );

create policy workspace_memberships_personal_api_key_manager_select
    on public.workspace_memberships
    for select to personal_api_key_manager
    using (user_id = (select private.personal_api_key_subject()) and status = 'active');

create policy workspace_memberships_personal_api_key_reader_select
    on public.workspace_memberships
    for select to personal_api_key_reader
    using (
        user_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
        and workspace_id = nullif(pg_catalog.current_setting('app.personal_api_key_workspace_id', true), '')::uuid
        and status = 'active'
    );

create policy account_lifecycle_personal_api_key_manager_select
    on public.account_lifecycle
    for select to personal_api_key_manager
    using (subject_id = (select private.personal_api_key_subject()) and state = 'available');

create policy account_lifecycle_personal_api_key_reader_select
    on public.account_lifecycle
    for select to personal_api_key_reader
    using (
        subject_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
        and state = 'available'
    );

create policy evidence_records_personal_api_key_reader_select
    on public.evidence_records
    for select to personal_api_key_reader
    using (
        workspace_id = nullif(pg_catalog.current_setting('app.personal_api_key_workspace_id', true), '')::uuid
        and pg_catalog.current_setting('app.personal_api_key_scope_evidence', true) = 'true'
        and exists (
            select 1 from public.workspace_memberships as membership
            where membership.user_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
              and membership.workspace_id = evidence_records.workspace_id
              and membership.status = 'active'
        )
    );

create policy evidence_relationships_personal_api_key_reader_select
    on public.evidence_relationships
    for select to personal_api_key_reader
    using (
        workspace_id = nullif(pg_catalog.current_setting('app.personal_api_key_workspace_id', true), '')::uuid
        and pg_catalog.current_setting('app.personal_api_key_scope_relationships', true) = 'true'
        and exists (
            select 1 from public.workspace_memberships as membership
            where membership.user_id = nullif(pg_catalog.current_setting('app.personal_api_key_user_id', true), '')::uuid
              and membership.workspace_id = evidence_relationships.workspace_id
              and membership.status = 'active'
        )
    );

create table private.personal_api_key_gateway_secrets (
    singleton boolean primary key default true check (singleton),
    secret bytea not null check (pg_catalog.octet_length(secret) = 32),
    updated_at timestamptz not null default pg_catalog.clock_timestamp()
);
alter table private.personal_api_key_gateway_secrets enable row level security;
alter table private.personal_api_key_gateway_secrets force row level security;
revoke all on table private.personal_api_key_gateway_secrets
    from public, anon, authenticated, service_role, personal_api_key_manager, personal_api_key_reader;
grant select (singleton, secret) on private.personal_api_key_gateway_secrets to personal_api_key_reader;
create policy personal_api_key_gateway_secrets_reader_select
    on private.personal_api_key_gateway_secrets
    for select to personal_api_key_reader
    using (singleton);

create table private.personal_api_key_gateway_nonces (
    nonce uuid primary key,
    expires_at timestamptz not null
);
create index personal_api_key_gateway_nonces_expiry_idx
    on private.personal_api_key_gateway_nonces (expires_at);
alter table private.personal_api_key_gateway_nonces enable row level security;
alter table private.personal_api_key_gateway_nonces force row level security;
revoke all on table private.personal_api_key_gateway_nonces
    from public, anon, authenticated, service_role, personal_api_key_manager, personal_api_key_reader;
grant insert, select, delete on private.personal_api_key_gateway_nonces to personal_api_key_reader;
create policy personal_api_key_gateway_nonces_reader_select
    on private.personal_api_key_gateway_nonces for select to personal_api_key_reader using (true);
create policy personal_api_key_gateway_nonces_reader_insert
    on private.personal_api_key_gateway_nonces for insert to personal_api_key_reader with check (true);
create policy personal_api_key_gateway_nonces_reader_delete
    on private.personal_api_key_gateway_nonces for delete to personal_api_key_reader using (true);

create function private.authorize_personal_api_key_read(
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
       or p_limit not between 1 and 101
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

create function public.create_personal_api_key(
    p_id uuid,
    p_workspace_id uuid,
    p_name text,
    p_expiration_days integer
)
returns table (
    id uuid,
    workspace_id uuid,
    name text,
    key_prefix text,
    scopes text[],
    created_at timestamptz,
    expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
declare
    actor uuid := private.personal_api_key_subject();
    request_headers jsonb;
    raw_hash text;
    active_keys bigint;
    prefix text;
begin
    request_headers := coalesce(
        nullif(pg_catalog.current_setting('request.headers', true), '')::jsonb,
        '{}'::jsonb
    );
    raw_hash := request_headers ->> 'x-evidence-api-key-hash';
    if actor is null
       or p_id is null
       or p_workspace_id is null
       or p_name is null
       or char_length(pg_catalog.btrim(p_name)) not between 1 and 80
       or raw_hash is null
       or raw_hash !~ '^[0-9a-f]{64}$'
       or p_expiration_days is null
       or p_expiration_days not between 1 and 90 then
        raise exception 'invalid personal API key request' using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(actor::text, 0));
    if not exists (
        select 1 from public.account_lifecycle as lifecycle
        where lifecycle.subject_id = actor and lifecycle.state = 'available'
    ) or not exists (
        select 1 from public.workspace_memberships as membership
        where membership.user_id = actor
          and membership.workspace_id = p_workspace_id
          and membership.status = 'active'
    ) then
        raise exception 'workspace access is unavailable' using errcode = '42501';
    end if;

    select pg_catalog.count(*) into active_keys
      from public.personal_api_keys as api_key
     where api_key.user_id = actor
       and api_key.revoked_at is null
       and api_key.expires_at > pg_catalog.statement_timestamp();
    if active_keys >= 5 then
        raise exception 'personal API key limit reached' using errcode = 'P4291';
    end if;

    prefix := 'evl_pk_' || pg_catalog.left(p_id::text, 8);
    return query
    insert into public.personal_api_keys (
        id, user_id, workspace_id, name, key_prefix, key_hash, scopes, expires_at
    ) values (
        p_id, actor, p_workspace_id, pg_catalog.btrim(p_name), prefix, raw_hash,
        array['evidence:read', 'relationships:read']::text[],
        pg_catalog.statement_timestamp() + (p_expiration_days * interval '1 day')
    )
    returning
        personal_api_keys.id,
        personal_api_keys.workspace_id,
        personal_api_keys.name,
        personal_api_keys.key_prefix,
        personal_api_keys.scopes,
        personal_api_keys.created_at,
        personal_api_keys.expires_at;
end;
$function$;

create function public.revoke_personal_api_key(p_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
declare
    actor uuid := private.personal_api_key_subject();
    revoked integer;
begin
    if actor is null then
        raise exception 'identity verification is required' using errcode = '42501';
    end if;
    update public.personal_api_keys
       set revoked_at = coalesce(revoked_at, pg_catalog.statement_timestamp())
     where id = p_id and user_id = actor;
    get diagnostics revoked = row_count;
    return revoked = 1;
end;
$function$;

create function public.read_personal_api_key_evidence(
    p_key_id uuid,
    p_workspace_id uuid,
    p_limit integer default 51,
    p_cursor_created_at text default null,
    p_cursor_id uuid default null
)
returns table (
    id uuid,
    workspace_id uuid,
    content text,
    source_label text,
    source_id uuid,
    source_version_id uuid,
    created_at timestamptz,
    updated_at timestamptz
)
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
begin
    perform private.authorize_personal_api_key_read(
        'evidence', p_key_id, p_workspace_id, p_limit, p_cursor_created_at, p_cursor_id
    );
    return query
    select record.id, record.workspace_id, record.content, record.source_label,
           record.source_id, record.source_version_id, record.created_at, record.updated_at
      from public.evidence_records as record
     where record.workspace_id = p_workspace_id
       and (
           p_cursor_created_at is null
           or record.created_at < p_cursor_created_at::timestamptz
           or (record.created_at = p_cursor_created_at::timestamptz and record.id < p_cursor_id)
       )
     order by record.created_at desc, record.id desc
     limit p_limit;
end;
$function$;

create function public.read_personal_api_key_relationships(
    p_key_id uuid,
    p_workspace_id uuid,
    p_limit integer default 51,
    p_cursor_created_at text default null,
    p_cursor_id uuid default null
)
returns table (
    id uuid,
    workspace_id uuid,
    evidence_id_a uuid,
    evidence_id_b uuid,
    relation_type text,
    rationale text,
    provenance_note text,
    created_by uuid,
    created_at timestamptz,
    updated_by uuid,
    updated_at timestamptz
)
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
begin
    perform private.authorize_personal_api_key_read(
        'relationships', p_key_id, p_workspace_id, p_limit, p_cursor_created_at, p_cursor_id
    );
    return query
    select relation.id, relation.workspace_id, relation.evidence_id_a, relation.evidence_id_b,
           relation.relation_type, relation.rationale, relation.provenance_note,
           relation.created_by, relation.created_at, relation.updated_by, relation.updated_at
      from public.evidence_relationships as relation
     where relation.workspace_id = p_workspace_id
       and (
           p_cursor_created_at is null
           or relation.created_at < p_cursor_created_at::timestamptz
           or (relation.created_at = p_cursor_created_at::timestamptz and relation.id < p_cursor_id)
       )
     order by relation.created_at desc, relation.id desc
     limit p_limit;
end;
$function$;

create function private.configure_personal_api_key_gateway_secret(p_secret_hex text)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
begin
    if p_secret_hex !~ '^[0-9a-f]{64}$' then
        raise exception 'gateway secret must be 32 random bytes encoded as lowercase hex'
            using errcode = '22023';
    end if;
    insert into private.personal_api_key_gateway_secrets (singleton, secret)
    values (true, pg_catalog.decode(p_secret_hex, 'hex'))
    on conflict (singleton) do update
        set secret = excluded.secret,
            updated_at = pg_catalog.clock_timestamp();
end;
$function$;

revoke all on function public.create_personal_api_key(uuid,uuid,text,integer)
    from public, anon, authenticated, service_role;
revoke all on function public.revoke_personal_api_key(uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.create_personal_api_key(uuid,uuid,text,integer)
    to authenticated;
grant execute on function public.revoke_personal_api_key(uuid)
    to authenticated;

revoke all on function public.read_personal_api_key_evidence(uuid,uuid,integer,text,uuid)
    from public, authenticated, service_role;
revoke all on function public.read_personal_api_key_relationships(uuid,uuid,integer,text,uuid)
    from public, authenticated, service_role;
grant execute on function public.read_personal_api_key_evidence(uuid,uuid,integer,text,uuid)
    to anon;
grant execute on function public.read_personal_api_key_relationships(uuid,uuid,integer,text,uuid)
    to anon;

revoke all on function private.configure_personal_api_key_gateway_secret(text)
    from public, anon, authenticated, service_role, personal_api_key_manager, personal_api_key_reader;
grant execute on function private.configure_personal_api_key_gateway_secret(text)
    to postgres;

grant create on schema public, private to personal_api_key_manager, personal_api_key_reader;

do $owners$
declare
    actor name := current_user;
begin
    execute pg_catalog.format(
        'grant personal_api_key_manager to %I with set true, inherit false', actor
    );
    execute 'alter function public.create_personal_api_key(uuid,uuid,text,integer) owner to personal_api_key_manager';
    execute 'alter function public.revoke_personal_api_key(uuid) owner to personal_api_key_manager';
    execute pg_catalog.format('revoke personal_api_key_manager from %I', actor);

    execute pg_catalog.format(
        'grant personal_api_key_reader to %I with set true, inherit false', actor
    );
    execute 'alter function private.authorize_personal_api_key_read(text,uuid,uuid,integer,text,uuid) owner to personal_api_key_reader';
    execute 'alter function public.read_personal_api_key_evidence(uuid,uuid,integer,text,uuid) owner to personal_api_key_reader';
    execute 'alter function public.read_personal_api_key_relationships(uuid,uuid,integer,text,uuid) owner to personal_api_key_reader';
    execute pg_catalog.format('revoke personal_api_key_reader from %I', actor);
end
$owners$;

revoke create on schema public, private from personal_api_key_manager, personal_api_key_reader;

comment on table public.personal_api_keys is
    'Workspace-scoped read-only API key verifiers. Raw API key secrets are never persisted or returned after creation.';
comment on role personal_api_key_manager is
    'NOLOGIN, NOINHERIT, NOBYPASSRLS definer role limited to key creation/revocation under the caller JWT and RLS.';
comment on role personal_api_key_reader is
    'NOLOGIN, NOINHERIT, NOBYPASSRLS definer role limited to signed read-only personal API key RPCs under forced RLS.';
