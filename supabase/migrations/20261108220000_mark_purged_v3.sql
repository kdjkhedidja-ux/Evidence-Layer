-- The worker-owned mark function is subject to the request RLS update policy.
-- The terminal mark is an owner-level bookkeeping step: mark v3 is owned by
-- postgres with row_security off, deletes any remaining lifecycle marker, and
-- records the terminal audit event. The worker calls it as postgres.
create function private.mark_account_purged_v3(p_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
set row_security = off
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid;
begin
    select * into request_row
      from public.data_deletion_requests
     where request_id = p_request_id
     for update;
    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;
    actor := request_row.requester_user_id;

    update public.data_deletion_requests
       set status = 'purged'
     where request_id = p_request_id
       and status in ('purge_pending', 'purged');

    delete from public.account_lifecycle
     where subject_id = actor;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id, 'account_purged', 'success',
        p_request_id, 'account', pg_catalog.jsonb_build_object('terminal', 'purged')
    )
    on conflict (request_id, action) do nothing;

    return pg_catalog.jsonb_build_object('status', 'purged', 'subject_id', actor);
end;
$function$;

revoke all on function private.mark_account_purged_v3(uuid)
    from public, anon, authenticated, service_role;
grant execute on function private.mark_account_purged_v3(uuid) to postgres;
grant execute on function private.mark_account_purged_v3(uuid)
    to account_deletion_worker;
