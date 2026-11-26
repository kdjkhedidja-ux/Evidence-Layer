-- Stop connector writes for accounts that are being deleted.
--
-- The background worker syncs every connected connection with the
-- service-role credential, which bypasses the account gate that protects
-- signed-in requests. Once an account deletion is accepted (lifecycle state
-- leaves 'available') the worker could still write fresh evidence and
-- connector documents until the purge ran.
--
-- 1. upsert_connector_evidence refuses writes for an owner whose lifecycle
--    state is not 'available' (both caller paths).
-- 2. connector_sync_allowed lets the worker skip such connections before it
--    fetches or stores anything (connector_documents included).
create or replace function public.upsert_connector_evidence(
    p_workspace_id uuid,
    p_connection_id uuid,
    p_documents jsonb
)
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
    v_uid uuid := auth.uid();
    v_claim_role text := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb ->> 'role';
    v_connection public.connector_connections;
    v_doc record;
    v_count integer := 0;
begin
    if v_uid is null then
        if v_claim_role is distinct from 'service_role' then
            raise exception 'authentication is required' using errcode = '42501';
        end if;
        select user_id into v_uid
          from public.connector_connections
         where connection_id = p_connection_id
           and workspace_id = p_workspace_id;
        if v_uid is null then
            raise exception 'connector evidence is not authorized' using errcode = '42501';
        end if;
    end if;
    select * into v_connection
      from public.connector_connections
     where connection_id = p_connection_id
       and user_id = v_uid
       and workspace_id = p_workspace_id;
    if not found then
        raise exception 'connector evidence is not authorized' using errcode = '42501';
    end if;
    if not exists (
        select 1
          from public.account_lifecycle
         where subject_id = v_uid
           and state = 'available'
    ) then
        raise exception 'connector evidence owner account is not available' using errcode = '42501';
    end if;
    if not exists (
        select 1
          from public.workspace_memberships
         where workspace_id = p_workspace_id
           and user_id = v_uid
           and role = 'owner'
           and status = 'active'
    ) then
        raise exception 'connector evidence requires a personal workspace owner' using errcode = '42501';
    end if;
    if p_documents is null or pg_catalog.jsonb_typeof(p_documents) <> 'array' then
        raise exception 'documents must be a JSON array' using errcode = '22023';
    end if;

    for v_doc in
        select content, source_label, connector_external_id, connector_revision
          from pg_catalog.jsonb_to_recordset(p_documents) as document(
              content text,
              source_label text,
              connector_external_id text,
              connector_revision text
          )
    loop
        if v_doc.connector_external_id is null
           or v_doc.connector_revision is null
           or v_doc.content is null
           or pg_catalog.length(v_doc.content) not between 1 and 65536 then
            raise exception 'invalid connector evidence document' using errcode = '22023';
        end if;
        insert into public.evidence_records (
            workspace_id, content, source_label,
            connector_connection_id, connector_external_id, connector_revision
        ) values (
            p_workspace_id, v_doc.content, pg_catalog.left(v_doc.source_label, 512),
            p_connection_id, v_doc.connector_external_id, v_doc.connector_revision
        )
        on conflict (connector_connection_id, connector_external_id) do update
           set content = excluded.content,
               source_label = excluded.source_label,
               connector_revision = excluded.connector_revision,
               updated_at = pg_catalog.clock_timestamp()
         where evidence_records.workspace_id = p_workspace_id
           and evidence_records.connector_connection_id = p_connection_id;
        v_count := v_count + 1;
    end loop;
    return v_count;
end;
$function$;

revoke all on function public.upsert_connector_evidence(uuid, uuid, jsonb)
    from public, anon;
grant execute on function public.upsert_connector_evidence(uuid, uuid, jsonb)
    to authenticated, service_role;

create function public.connector_sync_allowed(p_connection_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $function$
    select exists (
        select 1
          from public.connector_connections as connection
          join public.account_lifecycle as lifecycle
            on lifecycle.subject_id = connection.user_id
         where connection.connection_id = p_connection_id
           and lifecycle.state = 'available'
    );
$function$;

revoke all on function public.connector_sync_allowed(uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.connector_sync_allowed(uuid)
    to service_role;
