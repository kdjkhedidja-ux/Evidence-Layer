-- Single-document read for API-key clients/MCP get_document.
create function public.read_personal_api_key_evidence_document(
    p_key_id uuid,
    p_workspace_id uuid,
    p_evidence_id uuid
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
        'evidence', p_key_id, p_workspace_id, 1, null, null
    );
    return query
    select record.id, record.workspace_id, record.content, record.source_label,
           record.source_id, record.source_version_id, record.created_at, record.updated_at
      from public.evidence_records as record
     where record.workspace_id = p_workspace_id
       and record.id = p_evidence_id
     limit 1;
end;
$function$;

revoke all on function public.read_personal_api_key_evidence_document(uuid, uuid, uuid)
    from public, authenticated, service_role;
grant execute on function public.read_personal_api_key_evidence_document(uuid, uuid, uuid)
    to anon;
