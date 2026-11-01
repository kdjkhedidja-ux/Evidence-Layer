-- Explicit, reviewable relationships between evidence records in one workspace.
-- This additive table does not alter existing evidence or source rows.

create unique index evidence_records_workspace_id_id_key
    on public.evidence_records (workspace_id, id);

-- Owner-only evidence mutation closes the earlier MVP policy that let every
-- active member replace or remove every record in a shared workspace.
drop policy evidence_records_insert_active_members on public.evidence_records;
drop policy evidence_records_update_active_members on public.evidence_records;
drop policy evidence_records_delete_active_members on public.evidence_records;

create policy evidence_records_insert_active_owners
    on public.evidence_records
    for insert
    to authenticated
    with check (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy evidence_records_update_active_owners
    on public.evidence_records
    for update
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    )
    with check (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy evidence_records_delete_active_owners
    on public.evidence_records
    for delete
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_records.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create table public.evidence_relationships (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    evidence_id_a uuid not null,
    evidence_id_b uuid not null,
    relation_type text not null check (
        relation_type in ('supports', 'contradicts', 'contextualizes', 'derived_from')
    ),
    rationale text not null check (
        char_length(btrim(rationale)) between 1 and 4000
    ),
    provenance_note text not null check (
        char_length(btrim(provenance_note)) between 1 and 2000
    ),
    created_by uuid not null default auth.uid() references auth.users (id),
    created_at timestamptz not null default now(),
    updated_by uuid not null default auth.uid() references auth.users (id),
    updated_at timestamptz not null default now(),
    constraint evidence_relationships_distinct_ordered_pair
        check (evidence_id_a < evidence_id_b),
    constraint evidence_relationships_pair_type_unique
        unique (workspace_id, evidence_id_a, evidence_id_b, relation_type),
    constraint evidence_relationships_evidence_a_fkey
        foreign key (workspace_id, evidence_id_a)
        references public.evidence_records (workspace_id, id) on delete cascade,
    constraint evidence_relationships_evidence_b_fkey
        foreign key (workspace_id, evidence_id_b)
        references public.evidence_records (workspace_id, id) on delete cascade
);

create index evidence_relationships_workspace_idx
    on public.evidence_relationships (workspace_id, created_at desc, id);

alter table public.evidence_relationships enable row level security;
alter table public.evidence_relationships force row level security;

revoke all on table public.evidence_relationships
    from public, anon, authenticated, service_role;
grant select, insert, delete on table public.evidence_relationships to authenticated;
grant update (relation_type, rationale, provenance_note)
    on table public.evidence_relationships to authenticated;

create policy evidence_relationships_select_active_members
    on public.evidence_relationships
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_relationships.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.status = 'active'
        )
    );

create policy evidence_relationships_insert_active_owners
    on public.evidence_relationships
    for insert
    to authenticated
    with check (
        evidence_relationships.created_by = (select auth.uid())
        and evidence_relationships.updated_by = (select auth.uid())
        and exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_relationships.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy evidence_relationships_update_active_owners
    on public.evidence_relationships
    for update
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_relationships.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    )
    with check (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_relationships.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create policy evidence_relationships_delete_active_owners
    on public.evidence_relationships
    for delete
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = evidence_relationships.workspace_id
              and membership.user_id = (select auth.uid())
              and membership.role = 'owner'
              and membership.status = 'active'
        )
    );

create or replace function private.protect_evidence_relationship()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'UPDATE' then
        if new.id is distinct from old.id
            or new.workspace_id is distinct from old.workspace_id
            or new.evidence_id_a is distinct from old.evidence_id_a
            or new.evidence_id_b is distinct from old.evidence_id_b
            or new.created_by is distinct from old.created_by
            or new.created_at is distinct from old.created_at then
            raise exception 'relationship identity and provenance cannot be changed'
                using errcode = '42501';
        end if;
    end if;

    new.updated_by := auth.uid();
    new.updated_at := now();
    return new;
end;
$function$;

revoke all on function private.protect_evidence_relationship()
    from public, anon, authenticated, service_role;

create trigger evidence_relationships_protect_identity
    before insert or update on public.evidence_relationships
    for each row execute function private.protect_evidence_relationship();

comment on table public.evidence_relationships is
    'Explicit human-authored links between same-workspace evidence records; active members read and active owners write.';
