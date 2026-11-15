-- Workspace quota usage for owner-facing scale limits.
--
-- The service checks these counters before staging a source or creating an
-- invitation. The function is read-only and returns content-free counts.

create or replace function public.workspace_source_usage(
    p_workspace_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    version_count integer;
    total_bytes bigint;
    staged_count integer;
    pending_invitations integer;
begin
    if actor is null or not exists (
        select 1
        from public.workspace_memberships
        where workspace_id = p_workspace_id
          and user_id = actor
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'caller is not an active workspace owner'
            using errcode = '42501';
    end if;

    select count(*)::integer,
           coalesce(sum(snapshot.size_bytes), 0)::bigint,
           count(*) filter (where snapshot.state = 'staged')::integer
      into version_count, total_bytes, staged_count
      from public.source_snapshots as snapshot
     where snapshot.workspace_id = p_workspace_id;

    select count(*)::integer
      into pending_invitations
      from private.workspace_invitations as invitation
     where invitation.workspace_id = p_workspace_id
       and invitation.accepted_at is null
       and invitation.revoked_at is null
       and invitation.expires_at > pg_catalog.clock_timestamp();

    return pg_catalog.jsonb_build_object(
        'version_count', version_count,
        'total_bytes', total_bytes,
        'staged_count', staged_count,
        'pending_invitations', pending_invitations
    );
end;
$function$;

revoke all on function public.workspace_source_usage(uuid)
    from public, anon, service_role;
grant execute on function public.workspace_source_usage(uuid)
    to authenticated;

comment on function public.workspace_source_usage(uuid) is
    'Owner-only content-free quota counters for a workspace.';
