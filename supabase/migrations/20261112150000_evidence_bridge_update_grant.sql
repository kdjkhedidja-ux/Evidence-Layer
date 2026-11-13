-- PostgREST upsert updates workspace_id as part of the merge; the writer role
-- needs the column-level UPDATE grant in addition to INSERT.
grant update (workspace_id) on table public.evidence_records to authenticated;
