-- Workspace-owned evidence storage. Every user request still reaches this
-- table through the Supabase Data API with its verified user access token.

create table public.evidence_records (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    content text not null check (
        char_length(btrim(content)) between 1 and 65536
    ),
    source_label text check (
        source_label is null or char_length(source_label) <= 512
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index evidence_records_workspace_created_idx
    on public.evidence_records (workspace_id, created_at desc, id);

alter table public.evidence_records enable row level security;
alter table public.evidence_records force row level security;

-- Never inherit broad/default grants on a customer-data table. The service
-- intentionally has no service_role credential or service_role table grant.
revoke all on table public.evidence_records
    from public, anon, authenticated, service_role;
grant select on table public.evidence_records to authenticated;
grant insert (workspace_id, content, source_label)
    on table public.evidence_records to authenticated;
grant update (content, source_label)
    on table public.evidence_records to authenticated;
grant delete on table public.evidence_records to authenticated;

create policy evidence_records_select_active_members
    on public.evidence_records
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    );

create policy evidence_records_insert_active_members
    on public.evidence_records
    for insert
    to authenticated
    with check (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    );

create policy evidence_records_update_active_members
    on public.evidence_records
    for update
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    )
    with check (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    );

create policy evidence_records_delete_active_members
    on public.evidence_records
    for delete
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    );

create schema if not exists private;

create or replace function private.protect_evidence_record()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if new.workspace_id is distinct from old.workspace_id then
        raise exception 'evidence workspace ownership cannot be changed'
            using errcode = '42501';
    end if;

    new.updated_at := now();
    return new;
end;
$function$;

revoke all on function private.protect_evidence_record()
    from public, anon, authenticated, service_role;

create trigger evidence_records_protect_workspace_ownership
    before update on public.evidence_records
    for each row execute function private.protect_evidence_record();

comment on table public.evidence_records is
    'Workspace-owned evidence text. Active workspace membership is required for every user operation.';
