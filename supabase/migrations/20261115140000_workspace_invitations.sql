-- Workspace invitations.
--
-- An owner creates a one-time, expiring invitation for an email. The raw token
-- is returned once and only its SHA-256 hash is stored. An authenticated user
-- accepts the token and is added to the workspace as a member. Delivery is
-- intentionally out of scope for this migration; the owner can share the link
-- until an email delivery worker is added.

create table if not exists private.workspace_invitations (
    invitation_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    email text not null,
    role text not null default 'member' check (role in ('owner', 'member')),
    token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
    created_by uuid not null references auth.users (id) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    accepted_at timestamptz,
    accepted_by uuid references auth.users (id) on delete set null,
    revoked_at timestamptz,
    constraint workspace_invitations_email_shape check (
        length(email) between 3 and 320 and email = lower(email) and position('@' in email) > 1
    ),
    constraint workspace_invitations_expiry_shape check (
        expires_at > created_at and expires_at <= created_at + interval '30 days'
    )
);

create index workspace_invitations_pending_idx
    on private.workspace_invitations (workspace_id, email, expires_at)
    where accepted_at is null and revoked_at is null;

alter table private.workspace_invitations enable row level security;
alter table private.workspace_invitations force row level security;
revoke all on table private.workspace_invitations
    from public, anon, authenticated, service_role;

create or replace function public.create_workspace_invitation(
    p_workspace_id uuid,
    p_email text,
    p_token_hash text,
    p_expires_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    normalized_email text := lower(trim(p_email));
    invitation private.workspace_invitations;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    if not exists (
        select 1 from public.workspace_memberships
        where workspace_id = p_workspace_id
          and user_id = actor
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'caller is not an active workspace owner'
            using errcode = '42501';
    end if;
    if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid invitation token' using errcode = '22023';
    end if;
    if p_expires_at is null
       or p_expires_at <= pg_catalog.clock_timestamp()
       or p_expires_at > pg_catalog.clock_timestamp() + interval '30 days' then
        raise exception 'invalid invitation expiry' using errcode = '22023';
    end if;

    insert into private.workspace_invitations (
        workspace_id, email, role, token_hash, created_by, expires_at
    ) values (
        p_workspace_id, normalized_email, 'member', p_token_hash, actor, p_expires_at
    )
    returning * into invitation;

    return pg_catalog.jsonb_build_object(
        'invitation_id', invitation.invitation_id,
        'workspace_id', invitation.workspace_id,
        'email', invitation.email,
        'role', invitation.role,
        'created_at', invitation.created_at,
        'expires_at', invitation.expires_at
    );
end;
$function$;

create or replace function public.list_workspace_invitations(
    p_workspace_id uuid
)
returns table (
    out_invitation_id uuid,
    out_email text,
    out_role text,
    out_created_at timestamptz,
    out_expires_at timestamptz,
    out_accepted_at timestamptz,
    out_revoked_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    if actor is null or not exists (
        select 1 from public.workspace_memberships
        where workspace_id = p_workspace_id
          and user_id = actor
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'caller is not an active workspace owner'
            using errcode = '42501';
    end if;
    return query
    select invitation.invitation_id, invitation.email, invitation.role,
           invitation.created_at, invitation.expires_at,
           invitation.accepted_at, invitation.revoked_at
    from private.workspace_invitations as invitation
    where invitation.workspace_id = p_workspace_id
    order by invitation.created_at desc;
end;
$function$;

create or replace function public.revoke_workspace_invitation(
    p_invitation_id uuid
)
returns void
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    invitation private.workspace_invitations;
begin
    select * into invitation
    from private.workspace_invitations
    where invitation_id = p_invitation_id
    for update;
    if not found then
        raise exception 'invitation not found' using errcode = 'P0002';
    end if;
    if actor is null or not exists (
        select 1 from public.workspace_memberships
        where workspace_id = invitation.workspace_id
          and user_id = actor
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'caller is not an active workspace owner'
            using errcode = '42501';
    end if;
    update private.workspace_invitations
       set revoked_at = pg_catalog.clock_timestamp()
     where invitation_id = p_invitation_id
       and accepted_at is null
       and revoked_at is null;
end;
$function$;

create or replace function public.accept_workspace_invitation(
    p_token_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
    invitation private.workspace_invitations;
begin
    if actor is null then
        raise exception 'caller is not authenticated' using errcode = '42501';
    end if;
    if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid invitation token' using errcode = '22023';
    end if;

    select * into invitation
    from private.workspace_invitations
    where token_hash = p_token_hash
      and accepted_at is null
      and revoked_at is null
      and expires_at > pg_catalog.clock_timestamp()
    for update;
    if not found then
        raise exception 'invitation is not available' using errcode = 'P0002';
    end if;

    insert into public.workspace_memberships (workspace_id, user_id, role, status)
    values (invitation.workspace_id, actor, 'member', 'active')
    on conflict (workspace_id, user_id)
    do update set status = 'active';

    update private.workspace_invitations
       set accepted_at = pg_catalog.clock_timestamp(),
           accepted_by = actor
     where invitation_id = invitation.invitation_id;

    return pg_catalog.jsonb_build_object(
        'workspace_id', invitation.workspace_id,
        'role', 'member'
    );
end;
$function$;

revoke all on function public.create_workspace_invitation(uuid, text, text, timestamptz)
    from public, anon, service_role;
revoke all on function public.list_workspace_invitations(uuid)
    from public, anon, service_role;
revoke all on function public.revoke_workspace_invitation(uuid)
    from public, anon, service_role;
revoke all on function public.accept_workspace_invitation(text)
    from public, anon, service_role;

grant execute on function public.create_workspace_invitation(uuid, text, text, timestamptz)
    to authenticated;
grant execute on function public.list_workspace_invitations(uuid)
    to authenticated;
grant execute on function public.revoke_workspace_invitation(uuid)
    to authenticated;
grant execute on function public.accept_workspace_invitation(text)
    to authenticated;

comment on table private.workspace_invitations is
    'One-time, expiring workspace invitations; only the token hash is stored.';
