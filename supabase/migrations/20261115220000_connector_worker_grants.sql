-- Service-role grants for the isolated background connector sync worker.
-- The worker is server-only and has no inbound listener; these grants let it
-- read connections, write normalized documents/evidence, and update sync state
-- without a user session or MFA.
grant select, update on public.connector_connections to service_role;
grant select, insert, update on public.connector_documents to service_role;
create or replace function public.delete_stale_connector_evidence(
    p_connection_id uuid,
    p_current_external_ids text[]
)
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
    deleted integer;
begin
    delete from public.evidence_records
     where connector_connection_id = p_connection_id
       and (
           connector_external_id is null
           or not (connector_external_id = any(p_current_external_ids))
       );
    get diagnostics deleted = row_count;
    return deleted;
end;
$function$;

revoke all on function public.delete_stale_connector_evidence(uuid, text[])
    from public, anon, authenticated, service_role;
grant execute on function public.delete_stale_connector_evidence(uuid, text[])
    to service_role;
grant execute on function public.upsert_connector_evidence(uuid, uuid, jsonb)
    to service_role;
