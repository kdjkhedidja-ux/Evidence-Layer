-- ADR 0009 removed the external deletion journal. The manifest freeze is the
-- local durable acknowledgement: after the manifest row and its audit event
-- are written, move the request from journal_ack_pending to accepted so the
-- deletion worker can claim it atomically.
create or replace function private.audit_deletion_manifest_frozen()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
begin
    select * into request_row
    from public.data_deletion_requests
    where request_id = new.request_id;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        request_row.requester_user_id, null, 'deletion_manifest', new.manifest_id,
        'manifest_frozen', 'success', new.request_id, request_row.scope_type,
        pg_catalog.jsonb_build_object('target_type', new.target_type, 'ordinal', new.ordinal)
    );

    update public.data_deletion_requests
       set status = 'accepted'
     where request_id = new.request_id
       and status = 'journal_ack_pending';

    return new;
end;
$function$;
