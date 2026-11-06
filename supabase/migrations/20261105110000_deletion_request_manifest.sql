-- P1: account-scoped deletion request, frozen target manifest, and local acceptance.
-- This prepares purge; it does not execute purge or delete Storage objects.

alter table public.evidence_deletion_audit
    alter column workspace_id drop not null;

alter table public.evidence_deletion_audit
    add column request_id uuid,
    add column scope_type text;

alter table public.evidence_deletion_audit
    drop constraint evidence_deletion_audit_resource_type_check,
    drop constraint evidence_deletion_audit_action_check;

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_resource_type_check
        check (resource_type in ('evidence', 'relationship', 'deletion_request', 'deletion_manifest')),
    add constraint evidence_deletion_audit_action_check
        check (action in ('delete', 'request_created', 'request_accepted', 'manifest_frozen'));

alter table public.evidence_deletion_audit
    add constraint evidence_deletion_audit_scope_type_check
        check (scope_type is null or scope_type = 'account');

grant insert on table public.evidence_deletion_audit
    to account_lifecycle_transition_writer;

create policy evidence_deletion_audit_insert_transition_writer
    on public.evidence_deletion_audit
    for insert to account_lifecycle_transition_writer
    with check (
        actor_user_id = (select auth.uid())
        and request_id is not null
        and resource_type in ('deletion_request', 'deletion_manifest')
        and action in ('request_accepted', 'manifest_frozen')
        and result = 'success'
    );

create unique index evidence_deletion_audit_request_action_key
    on public.evidence_deletion_audit (request_id, action)
    where request_id is not null;

create table public.data_deletion_requests (
    request_id uuid primary key default pg_catalog.gen_random_uuid(),
    requester_user_id uuid not null references auth.users (id) on delete restrict,
    scope_type text not null check (scope_type = 'account'),
    scope_id uuid not null references auth.users (id) on delete restrict,
    status text not null default 'approval_pending'
        check (status in ('approval_pending', 'journal_ack_pending', 'accepted')),
    idempotency_key text not null check (pg_catalog.length(idempotency_key) between 1 and 256),
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    accepted_at timestamptz,
    constraint data_deletion_requests_account_scope_check
        check (scope_id = requester_user_id),
    constraint data_deletion_requests_acceptance_time_check
        check ((status = 'approval_pending' and accepted_at is null)
            or (status <> 'approval_pending' and accepted_at is not null))
);

create unique index data_deletion_requests_open_scope_key
    on public.data_deletion_requests (requester_user_id, scope_type)
    where status in ('approval_pending', 'journal_ack_pending');

create unique index data_deletion_requests_idempotency_key
    on public.data_deletion_requests (requester_user_id, idempotency_key);

alter table public.account_lifecycle
    add constraint account_lifecycle_request_id_fkey
        foreign key (request_id)
        references public.data_deletion_requests (request_id)
        on delete restrict;

alter table public.data_deletion_requests enable row level security;
alter table public.data_deletion_requests force row level security;
revoke all on table public.data_deletion_requests
    from public, anon, authenticated, service_role;
grant usage on schema public to authenticated;
grant insert (request_id, requester_user_id, scope_type, scope_id, idempotency_key)
    on table public.data_deletion_requests to authenticated;
grant select (request_id, requester_user_id, scope_type, scope_id, status, created_at, accepted_at)
    on table public.data_deletion_requests to authenticated;

create policy data_deletion_requests_insert_self
    on public.data_deletion_requests
    for insert to authenticated
    with check (
        requester_user_id = (select auth.uid())
        and scope_id = (select auth.uid())
        and scope_type = 'account'
        and status = 'approval_pending'
        and accepted_at is null
        and exists (
            select 1
            from public.account_lifecycle as lifecycle
            where lifecycle.subject_id = (select auth.uid())
              and lifecycle.state = 'available'
        )
    );

create policy data_deletion_requests_select_self
    on public.data_deletion_requests
    for select to authenticated
    using (requester_user_id = (select auth.uid()));

grant select on table public.data_deletion_requests
    to account_lifecycle_transition_writer;
grant update (status, accepted_at)
    on table public.data_deletion_requests to account_lifecycle_transition_writer;

create policy data_deletion_requests_transition_writer_select
    on public.data_deletion_requests
    for select to account_lifecycle_transition_writer
    using (true);

create policy data_deletion_requests_transition_writer_update
    on public.data_deletion_requests
    for update to account_lifecycle_transition_writer
    using (true)
    with check (status in ('journal_ack_pending', 'accepted') and accepted_at is not null);

create table public.data_deletion_manifest (
    manifest_id uuid primary key default pg_catalog.gen_random_uuid(),
    request_id uuid not null references public.data_deletion_requests (request_id) on delete restrict,
    ordinal integer not null check (ordinal > 0),
    target_type text not null check (target_type = 'account'),
    target_id uuid not null references auth.users (id) on delete restrict,
    frozen_at timestamptz not null default pg_catalog.clock_timestamp(),
    constraint data_deletion_manifest_request_id_ordinal_key unique (request_id, ordinal)
);

create unique index data_deletion_manifest_request_id_target_type_target_id_key
    on public.data_deletion_manifest (request_id, target_type, target_id);

alter table public.data_deletion_manifest enable row level security;
alter table public.data_deletion_manifest force row level security;
revoke all on table public.data_deletion_manifest
    from public, anon, authenticated, service_role;
grant select on table public.data_deletion_manifest to authenticated;
grant select, insert on table public.data_deletion_manifest
    to account_lifecycle_transition_writer;

create policy data_deletion_manifest_select_self
    on public.data_deletion_manifest
    for select to authenticated
    using (
        exists (
            select 1
            from public.data_deletion_requests as request
            where request.request_id = data_deletion_manifest.request_id
              and request.requester_user_id = (select auth.uid())
        )
    );

create policy data_deletion_manifest_transition_writer_select
    on public.data_deletion_manifest
    for select to account_lifecycle_transition_writer
    using (true);

create policy data_deletion_manifest_transition_writer_insert
    on public.data_deletion_manifest
    for insert to account_lifecycle_transition_writer
    with check (
        exists (
            select 1
            from public.data_deletion_requests as request
            where request.request_id = data_deletion_manifest.request_id
              and request.status = 'journal_ack_pending'
              and request.requester_user_id = data_deletion_manifest.target_id
        )
    );

create function private.enforce_deletion_request_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    if actor is null then
        raise exception 'deletion request requires an authenticated actor' using errcode = '42501';
    end if;
    new.requester_user_id := actor;
    new.scope_type := 'account';
    new.scope_id := actor;
    new.status := 'approval_pending';
    new.created_at := pg_catalog.clock_timestamp();
    new.accepted_at := null;
    return new;
end;
$function$;

create function private.reject_deletion_manifest_mutation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
    raise exception 'deletion manifest is immutable' using errcode = '42501';
end;
$function$;

create function private.audit_deletion_request_created()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        request_id, scope_type, metadata
    ) values (
        new.requester_user_id, null, 'deletion_request', new.request_id,
        'request_created', 'success', new.request_id, new.scope_type, '{}'::jsonb
    );
    return new;
end;
$function$;

create function private.audit_deletion_manifest_frozen()
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
    return new;
end;
$function$;

create function public.accept_account_deletion(p_request_id uuid)
returns public.data_deletion_requests
language plpgsql
security definer
set search_path = ''
as $function$
declare
    request_row public.data_deletion_requests;
    actor uuid := nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
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

-- ALTER OWNER requires SET on the target role; grant temporarily to the migration actor.
do $$
begin
    execute format('grant account_lifecycle_transition_writer to %I with set true, inherit false', current_user);
end
$$;

alter function public.accept_account_deletion(uuid)
    owner to account_lifecycle_transition_writer;

do $$
begin
    execute format('revoke account_lifecycle_transition_writer from %I', current_user);
end
$$;

revoke create on schema public from account_lifecycle_transition_writer;

revoke all on function private.enforce_deletion_request_insert() from public, anon, authenticated, service_role;
revoke all on function private.reject_deletion_manifest_mutation() from public, anon, authenticated, service_role;
revoke all on function private.audit_deletion_request_created() from public, anon, authenticated, service_role;
revoke all on function private.audit_deletion_manifest_frozen() from public, anon, authenticated, service_role;
revoke all on function public.accept_account_deletion(uuid) from public, anon, service_role;
grant execute on function public.accept_account_deletion(uuid) to authenticated;

create trigger data_deletion_requests_validate_insert
    before insert on public.data_deletion_requests
    for each row execute function private.enforce_deletion_request_insert();

create trigger data_deletion_requests_audit_created
    after insert on public.data_deletion_requests
    for each row execute function private.audit_deletion_request_created();

create trigger data_deletion_manifest_append_only
    before update or delete on public.data_deletion_manifest
    for each row execute function private.reject_deletion_manifest_mutation();

create trigger data_deletion_manifest_audit_frozen
    after insert on public.data_deletion_manifest
    for each row execute function private.audit_deletion_manifest_frozen();

comment on table public.data_deletion_requests is
    'Account-scoped deletion intent and local acceptance state; acceptance fences the account but does not execute purge.';
comment on table public.data_deletion_manifest is
    'Immutable type/id-only target references frozen for a deletion request; no user content.';
