-- Audit identifiers are historical values, not live foreign keys. Dropping
-- these FKs prevents an ON DELETE SET NULL action from mutating append-only
-- audit rows when a workspace or Auth identity is deleted.
alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_actor_user_id_fkey;

alter table public.evidence_deletion_audit
    drop constraint if exists evidence_deletion_audit_workspace_id_fkey;

-- The account-scoped request API is called with the user's token. RLS still
-- limits rows to the requester; the table grant is required by PostgREST.
grant select, insert on table public.data_deletion_requests to authenticated;

comment on column public.evidence_deletion_audit.actor_user_id is
    'Historical actor UUID retained after account deletion; not a live foreign key.';
comment on column public.evidence_deletion_audit.workspace_id is
    'Historical workspace UUID retained after workspace deletion; not a live foreign key.';
