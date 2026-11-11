-- Narrowed Phase 8 export storage and mediated grants.
--
-- Scope is account/personal only. Artifacts are stored as base64 in a forced-RLS
-- table and are never addressable through the Storage API. All access goes
-- through the security-definer RPCs below; clients have no table privileges.

create table public.data_export_jobs (
    export_id uuid primary key,
    requester_user_id uuid not null references auth.users (id) on delete cascade,
    scope_type text not null check (scope_type = 'account'),
    scope_id uuid not null,
    status text not null check (
        status in ('building', 'ready', 'revoked', 'expired', 'failed')
    ),
    format_version text not null,
    artifact_base64 text,
    artifact_sha256 text,
    artifact_size_bytes bigint,
    manifest jsonb,
    manifest_sha256 text,
    idempotency_key text not null,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    ready_at timestamptz,
    expires_at timestamptz,
    deleted_at timestamptz,
    error_category text,
    constraint data_export_jobs_idempotency_key
        unique (requester_user_id, idempotency_key),
    constraint data_export_jobs_artifact_shape check (
        (status = 'ready'
            and artifact_base64 is not null
            and artifact_sha256 ~ '^[0-9a-f]{64}$'
            and artifact_size_bytes is not null
            and artifact_size_bytes >= 0
            and manifest is not null
            and manifest_sha256 ~ '^[0-9a-f]{64}$'
            and ready_at is not null
            and expires_at is not null
            and expires_at > ready_at)
        or status <> 'ready'
    )
);

create index data_export_jobs_requester_created_idx
    on public.data_export_jobs (requester_user_id, created_at desc);
create index data_export_jobs_expiry_idx
    on public.data_export_jobs (expires_at)
    where artifact_base64 is not null;

create table public.data_export_grants (
    grant_id uuid primary key default pg_catalog.gen_random_uuid(),
    export_id uuid not null unique
        references public.data_export_jobs (export_id) on delete cascade,
    requester_user_id uuid not null references auth.users (id) on delete cascade,
    token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
    issued_at timestamptz not null default pg_catalog.clock_timestamp(),
    expires_at timestamptz not null,
    redeemed_at timestamptz,
    revoked_at timestamptz,
    constraint data_export_grants_expiry_check check (expires_at > issued_at)
);

create index data_export_grants_requester_idx
    on public.data_export_grants (requester_user_id, issued_at);

alter table public.data_export_jobs enable row level security;
alter table public.data_export_jobs force row level security;
alter table public.data_export_grants enable row level security;
alter table public.data_export_grants force row level security;

revoke all on table public.data_export_jobs
    from public, anon, authenticated, service_role;
revoke all on table public.data_export_grants
    from public, anon, authenticated, service_role;

create function public.finalize_account_export(
    p_export_id uuid,
    p_idempotency_key text,
    p_artifact_base64 text,
    p_artifact_sha256 text,
    p_artifact_size bigint,
    p_manifest jsonb,
    p_manifest_sha256 text,
    p_grant_token_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    v_uid uuid := auth.uid();
    v_now timestamptz := pg_catalog.clock_timestamp();
    v_size bigint;
    v_existing public.data_export_jobs;
begin
    if v_uid is null then
        raise exception 'authentication is required' using errcode = '42501';
    end if;
    if not exists (
        select 1
          from public.account_lifecycle
         where subject_id = v_uid
           and state = 'available'
    ) then
        raise exception 'account is unavailable' using errcode = '42501';
    end if;
    if p_idempotency_key is null
       or pg_catalog.length(pg_catalog.btrim(p_idempotency_key)) < 1
       or pg_catalog.length(p_idempotency_key) > 128 then
        raise exception 'invalid idempotency key' using errcode = '22023';
    end if;
    if p_artifact_base64 is null
       or pg_catalog.length(p_artifact_base64) > 12000000 then
        raise exception 'export artifact is too large' using errcode = '54000';
    end if;
    v_size := pg_catalog.octet_length(
        pg_catalog.decode(p_artifact_base64, 'base64')
    );
    if v_size <> p_artifact_size or v_size > 8388608 then
        raise exception 'export artifact size is invalid' using errcode = '54000';
    end if;
    if p_artifact_sha256 !~ '^[0-9a-f]{64}$'
       or p_manifest_sha256 !~ '^[0-9a-f]{64}$'
       or p_grant_token_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'export digest is invalid' using errcode = '22023';
    end if;

    select * into v_existing
      from public.data_export_jobs
     where requester_user_id = v_uid
       and idempotency_key = p_idempotency_key
     for update;
    if found then
        return pg_catalog.jsonb_build_object(
            'export_id', v_existing.export_id,
            'status', v_existing.status,
            'ready_at', v_existing.ready_at,
            'expires_at', v_existing.expires_at,
            'reused', true
        );
    end if;

    insert into public.data_export_jobs (
        export_id, requester_user_id, scope_type, scope_id, status,
        format_version, artifact_base64, artifact_sha256,
        artifact_size_bytes, manifest, manifest_sha256, idempotency_key,
        ready_at, expires_at
    ) values (
        p_export_id, v_uid, 'account', v_uid, 'ready',
        'evidence-layer-export-v1', p_artifact_base64, p_artifact_sha256,
        v_size, p_manifest, p_manifest_sha256, p_idempotency_key,
        v_now, v_now + interval '24 hours'
    );

    insert into public.data_export_grants (
        export_id, requester_user_id, token_hash, expires_at
    ) values (
        p_export_id, v_uid, p_grant_token_hash, v_now + interval '24 hours'
    );

    return pg_catalog.jsonb_build_object(
        'export_id', p_export_id,
        'status', 'ready',
        'ready_at', v_now,
        'expires_at', v_now + interval '24 hours',
        'reused', false
    );
end;
$function$;

create function public.redeem_account_export_grant(
    p_export_id uuid,
    p_token_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    v_uid uuid := auth.uid();
    v_now timestamptz := pg_catalog.clock_timestamp();
    v_grant public.data_export_grants;
    v_export public.data_export_jobs;
begin
    if v_uid is null then
        raise exception 'authentication is required' using errcode = '42501';
    end if;
    if not exists (
        select 1
          from public.account_lifecycle
         where subject_id = v_uid
           and state = 'available'
    ) then
        raise exception 'account is unavailable' using errcode = '42501';
    end if;

    select grant_row.* into v_grant
      from public.data_export_grants as grant_row
     where grant_row.export_id = p_export_id
       and grant_row.requester_user_id = v_uid
       and grant_row.token_hash = p_token_hash
       and grant_row.redeemed_at is null
       and grant_row.revoked_at is null
       and grant_row.expires_at > v_now
     for update;
    if not found then
        return null;
    end if;

    select export_row.* into v_export
      from public.data_export_jobs as export_row
     where export_row.export_id = p_export_id
       and export_row.requester_user_id = v_uid
       and export_row.status = 'ready'
       and export_row.expires_at > v_now
       and export_row.artifact_base64 is not null;
    if not found then
        return null;
    end if;

    update public.data_export_grants
       set redeemed_at = v_now
     where grant_id = v_grant.grant_id;

    return pg_catalog.jsonb_build_object(
        'artifact_base64', v_export.artifact_base64,
        'artifact_sha256', v_export.artifact_sha256,
        'artifact_size_bytes', v_export.artifact_size_bytes,
        'expires_at', v_export.expires_at
    );
end;
$function$;

create function public.revoke_account_export(p_export_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    v_uid uuid := auth.uid();
    v_now timestamptz := pg_catalog.clock_timestamp();
begin
    if v_uid is null then
        raise exception 'authentication is required' using errcode = '42501';
    end if;
    if not exists (
        select 1
          from public.account_lifecycle
         where subject_id = v_uid
           and state = 'available'
    ) then
        raise exception 'account is unavailable' using errcode = '42501';
    end if;

    update public.data_export_jobs
       set status = 'revoked',
           artifact_base64 = null,
           artifact_sha256 = null,
           artifact_size_bytes = null,
           deleted_at = v_now
     where export_id = p_export_id
       and requester_user_id = v_uid
       and status in ('ready', 'building');
    if not found then
        return null;
    end if;

    update public.data_export_grants
       set revoked_at = v_now
     where export_id = p_export_id
       and revoked_at is null;

    return pg_catalog.jsonb_build_object(
        'export_id', p_export_id,
        'status', 'revoked'
    );
end;
$function$;

create function private.cleanup_expired_exports()
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
    v_now timestamptz := pg_catalog.clock_timestamp();
    v_cleared integer := 0;
begin
    update public.data_export_jobs
       set status = 'expired',
           artifact_base64 = null,
           artifact_sha256 = null,
           artifact_size_bytes = null,
           deleted_at = v_now
     where expires_at < v_now
       and artifact_base64 is not null;
    get diagnostics v_cleared = row_count;

    update public.data_export_grants
       set revoked_at = v_now
     where expires_at < v_now
       and revoked_at is null;
    return v_cleared;
end;
$function$;

revoke all on function public.finalize_account_export(
    uuid, text, text, text, bigint, jsonb, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.redeem_account_export_grant(uuid, text)
    from public, anon, authenticated, service_role;
revoke all on function public.revoke_account_export(uuid)
    from public, anon, authenticated, service_role;
revoke all on function private.cleanup_expired_exports()
    from public, anon, authenticated, service_role;

grant execute on function public.finalize_account_export(
    uuid, text, text, text, bigint, jsonb, text, text
) to authenticated;
grant execute on function public.redeem_account_export_grant(uuid, text)
    to authenticated;
grant execute on function public.revoke_account_export(uuid)
    to authenticated;
