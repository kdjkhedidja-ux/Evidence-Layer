-- Wave 2: transaction-bound audit evidence for the existing data DELETE paths.
-- The trigger is deliberately database-side: the caller's verified JWT supplies
-- auth.uid(), and the audit row commits or rolls back with the DELETE.

create table public.evidence_deletion_audit (
    event_id uuid primary key default pg_catalog.gen_random_uuid(),
    occurred_at timestamptz not null default pg_catalog.clock_timestamp(),
    actor_user_id uuid not null references auth.users (id) on delete restrict,
    workspace_id uuid not null references public.workspaces (id) on delete restrict,
    resource_type text not null check (resource_type in ('evidence', 'relationship')),
    resource_id uuid not null,
    action text not null check (action = 'delete'),
    result text not null check (result = 'success'),
    metadata jsonb not null default '{}'::jsonb,
    constraint evidence_deletion_audit_metadata_object
        check (pg_catalog.jsonb_typeof(metadata) = 'object')
);

alter table public.evidence_deletion_audit enable row level security;
alter table public.evidence_deletion_audit force row level security;

revoke all on table public.evidence_deletion_audit
    from public, anon, authenticated, service_role;

-- Keep the policy inventory explicit even though API roles have no table grants.
-- The trigger is the only write path and runs as its trusted function owner.
create policy evidence_deletion_audit_select_denied
    on public.evidence_deletion_audit
    as restrictive for select to authenticated using (false);

create policy evidence_deletion_audit_insert_denied
    on public.evidence_deletion_audit
    as restrictive for insert to authenticated with check (false);

create policy evidence_deletion_audit_update_denied
    on public.evidence_deletion_audit
    as restrictive for update to authenticated using (false) with check (false);

create policy evidence_deletion_audit_delete_denied
    on public.evidence_deletion_audit
    as restrictive for delete to authenticated using (false);

create or replace function private.audit_evidence_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    if actor is null then
        raise exception 'deletion audit requires an authenticated actor'
            using errcode = '42501';
    end if;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result
    ) values (
        actor, old.workspace_id, 'evidence', old.id, 'delete', 'success'
    );
    return old;
end;
$function$;

create or replace function private.audit_relationship_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
    actor uuid := auth.uid();
begin
    -- A relationship removed by an evidence FK cascade is a dependent effect,
    -- not a second client DELETE event. The evidence event is authoritative.
    if pg_catalog.pg_trigger_depth() > 1 then
        return old;
    end if;
    if actor is null then
        raise exception 'deletion audit requires an authenticated actor'
            using errcode = '42501';
    end if;

    insert into public.evidence_deletion_audit (
        actor_user_id, workspace_id, resource_type, resource_id, action, result,
        metadata
    ) values (
        actor, old.workspace_id, 'relationship', old.id, 'delete', 'success',
        pg_catalog.jsonb_build_object('relationship_type', old.relation_type)
    );
    return old;
end;
$function$;

revoke all on function private.audit_evidence_delete() from public, anon, authenticated, service_role;
revoke all on function private.audit_relationship_delete() from public, anon, authenticated, service_role;

create trigger evidence_records_audit_delete
    after delete on public.evidence_records
    for each row execute function private.audit_evidence_delete();

create trigger evidence_relationships_audit_delete
    after delete on public.evidence_relationships
    for each row execute function private.audit_relationship_delete();

create or replace function private.reject_evidence_deletion_audit_mutation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
    raise exception 'evidence deletion audit is append-only'
        using errcode = '42501';
end;
$function$;

revoke all on function private.reject_evidence_deletion_audit_mutation()
    from public, anon, authenticated, service_role;

create trigger evidence_deletion_audit_append_only
    before update or delete on public.evidence_deletion_audit
    for each row execute function private.reject_evidence_deletion_audit_mutation();

comment on table public.evidence_deletion_audit is
    'Transaction-bound, content-free audit events for existing evidence and relationship DELETE paths; trigger-written and append-only.';
