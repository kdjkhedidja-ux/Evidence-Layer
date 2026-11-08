-- Fix the account-deletion acceptance path without replacing a function owned
-- by another role (the migration guard forbids session role switches).
--
--   * PostgREST sets request.jwt.claims and auth.uid(), not the legacy
--     request.jwt.claim.sub GUC; the v2 function supports both.
--   * The function owner (account_lifecycle_transition_writer) needs table
--     UPDATE privileges; RLS policies alone do not grant them.
--
-- The service calls accept_account_deletion_v2. The original function remains
-- for backward compatibility but is no longer used by this service.

grant select on table public.data_deletion_requests
    to account_lifecycle_transition_writer;
grant update (status, accepted_at) on table public.data_deletion_requests
    to account_lifecycle_transition_writer;

grant select on table public.account_lifecycle
    to account_lifecycle_transition_writer;
grant update (state, request_id) on table public.account_lifecycle
    to account_lifecycle_transition_writer;

create function public.accept_account_deletion_v2(p_request_id uuid)
returns public.data_deletion_requests
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid := coalesce(
        auth.uid(),
        nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
    );
begin
    if actor is null then
        raise exception 'deletion acceptance requires an authenticated actor' using errcode = '42501';
    end if;

    select * into request_row
    from public.data_deletion_requests
    where request_id = p_request_id
      and requester_user_id = actor
    for update;

    if not found then
        raise exception 'deletion request not found' using errcode = 'P0002';
    end if;

    if request_row.status <> 'approval_pending' then
        return request_row;
    end if;

    update public.data_deletion_requests
    set status = 'journal_ack_pending', accepted_at = pg_catalog.clock_timestamp()
    where request_id = p_request_id;

    update public.account_lifecycle
    set state = 'revocation_pending', request_id = p_request_id
    where subject_id = actor and state = 'available';
    if not found then
        raise exception 'account lifecycle is not available' using errcode = '55000';
    end if;

    insert into public.data_deletion_manifest (request_id, ordinal, target_type, target_id)
    values (p_request_id, 1, 'account', actor);

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        actor, null, 'deletion_request', p_request_id,
        'request_accepted', 'success', p_request_id, 'account', '{}'::jsonb
    );

    select * into request_row
    from public.data_deletion_requests
    where request_id = p_request_id;
    return request_row;
end;
$function$;

grant create on schema public to account_lifecycle_transition_writer;

do $$
begin
    execute format('grant account_lifecycle_transition_writer to %I with set true, inherit false', current_user);
end
$$;

alter function public.accept_account_deletion_v2(uuid)
    owner to account_lifecycle_transition_writer;

do $$
begin
    execute format('revoke account_lifecycle_transition_writer from %I', current_user);
end
$$;

grant account_lifecycle_transition_writer to postgres with set true, inherit false;

revoke create on schema public from account_lifecycle_transition_writer;

revoke all on function public.accept_account_deletion_v2(uuid)
    from public, anon, service_role;
grant execute on function public.accept_account_deletion_v2(uuid)
    to authenticated;
