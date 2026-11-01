-- Optional provenance link from an evidence record to an exact published
-- source snapshot version. Both columns stay nullable; existing rows and
-- clients that never set a link are unaffected (no backfill, no rewrite).
--
-- Integrity model:
--   * The pair is all-or-nothing: a record cannot name a source without
--     pinning the exact version it was derived from.
--   * The foreign key guarantees the linked snapshot row exists in the same
--     workspace. It does not grant visibility: the Evidence Service validates
--     that the version is published and visible to the requesting user before
--     accepting a link, and all reads still flow through RLS with the user's
--     own access token.
--   * No privilege changes: RLS policies on evidence_records are untouched,
--     and only the two new columns are granted for insert/update.

alter table public.evidence_records
    add column source_id uuid null,
    add column source_version_id uuid null;

alter table public.evidence_records
    add constraint evidence_records_source_link_pair
        check ((source_id is null) = (source_version_id is null)),
    add constraint evidence_records_source_link_fkey
        foreign key (workspace_id, source_id, source_version_id)
        references public.source_snapshots (workspace_id, source_id, version_id)
        on delete restrict;

create index evidence_records_source_link_idx
    on public.evidence_records (workspace_id, source_id, source_version_id)
    where source_id is not null;

grant insert (source_id, source_version_id)
    on public.evidence_records to authenticated;
grant update (source_id, source_version_id)
    on public.evidence_records to authenticated;
