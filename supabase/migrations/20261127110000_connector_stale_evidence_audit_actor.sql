-- Let the background connector worker remove stale connector evidence.
--
-- delete_stale_connector_evidence (20261115220000) is called by the
-- service-role worker, which has no auth.uid(). The evidence delete audit
-- trigger required auth.uid() or an account deletion request, so the first
-- stale row (a deleted or renamed repository file, or an obsolete part of a
-- large file) raised 42501 "deletion audit requires an authenticated actor".
-- The worker cycle then failed after its upserts and stale evidence stayed
-- retrievable.
--
-- For a service-role caller deleting a connector evidence row, the actor is
-- the owner of that row's connector connection, and the audit event is marked
-- {"connector_worker": true}. This mirrors the service-role path of
-- upsert_connector_evidence (20261126120000). Every other case is unchanged:
-- a signed-in delete uses auth.uid(), the account deletion worker uses its
-- request, and anything else still fails closed.
create or replace function private.audit_evidence_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    connector_worker boolean := false;
begin
    if actor is null then
        select request.scope_id into actor
        from public.data_deletion_requests as request
        where request.request_id = nullif(current_setting('app.deletion_request_id', true), '')::uuid;
    end if;
    if actor is null
       and old.connector_connection_id is not null
       and (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
           = 'service_role' then
        select connection.user_id into actor
        from public.connector_connections as connection
        where connection.connection_id = old.connector_connection_id
          and connection.workspace_id = old.workspace_id;
        connector_worker := actor is not null;
    end if;
    if actor is null then
        raise exception 'deletion audit requires an authenticated actor'
            using errcode = '42501';
    end if;
    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result, metadata
    ) values (
        actor, old.workspace_id, 'evidence', old.id, 'delete', 'success',
        case
            when nullif(current_setting('app.deletion_request_id', true), '') is not null
                then pg_catalog.jsonb_build_object('worker_slice', true)
            when connector_worker
                then pg_catalog.jsonb_build_object('connector_worker', true)
            else '{}'::jsonb
        end
    );
    return old;
end;
$function$;

revoke all on function private.audit_evidence_delete() from public, anon, authenticated, service_role;
