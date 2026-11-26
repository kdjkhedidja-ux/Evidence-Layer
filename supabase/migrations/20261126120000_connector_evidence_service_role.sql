-- Let the background connector worker write connector evidence.
--
-- 20261115220000 granted service_role EXECUTE on upsert_connector_evidence,
-- but the function still required auth.uid(), which is null for the
-- service-role credential. Every worker sync therefore failed at the evidence
-- write, so changed GitHub files never reached evidence_records.
--
-- For a service-role caller the acting user is taken from the connection row
-- itself; the same connection/workspace/owner checks then apply unchanged.
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
