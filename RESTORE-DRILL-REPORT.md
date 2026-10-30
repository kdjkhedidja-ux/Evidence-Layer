# Evidence-test Restore Drill Report

## Outcome

The PostgreSQL custom archive was restored successfully to an isolated local
PostgreSQL 17.11 database. This is an initial restore validation, not proof of
complete equivalence with the source project or of Supabase service behavior.

## Source archive

- Local file: `/home/codespace/backups/Evidence-test/evidence-test-20261029T120239Z.dump`
- Size: `457512` bytes
- SHA-256: `08a6507b5edf6f2e8b72ecd85f4a686fbdb150c04455ae0e89958c5e1a5fb887`
- The archive was mounted read-only during inspection and restore.
- The Google Drive copy was not modified or independently re-verified in this drill.

## Restore destination

- Database: `evidence_restore_full`
- Container: `evidence-test-restore-20261029-1702-c0e5946`
- Image: `supabase/postgres:17.11.0.004`
- Volume: `evidence-test-restore-20261029-1702-c0e5946`
- Network mode: `none`; no published ports; restart policy disabled.
- At report time, the container was running and healthy.
- The failed earlier test container and its volume remain present:
  `evidence-test-restore-20261029-1659-c0e5946` (container exited with code 2).

## Restore evidence

The successful invocation was:

```text
docker exec evidence-test-restore-20261029-1702-c0e5946 \
  pg_restore --exit-on-error --verbose --no-password \
  -U supabase_admin -d evidence_restore_full \
  /restore/evidence-test.dump
```

The recorded process exit code was `0`, with zero warnings or errors in the
captured summary. Full verbose output was not persisted, so the complete
per-object restore log is unavailable for independent review.

The Supabase image lacked the archive's `supabase_realtime_admin` role; a local
`NOLOGIN NOINHERIT NOBYPASSRLS` role was added in the isolated cluster. To create
the archived `ensure_rls` event trigger, `postgres` was temporarily granted
`SUPERUSER` within that isolated container, then returned to `NOSUPERUSER`.
Read-only inspection confirmed `postgres` is currently not a superuser.

## Verified state

Read-only catalog checks after restore observed:

- 11 non-system schemas (10 archive schemas plus the default `public` schema).
- 43 tables; all 42 archived table entries were present. The additional
  `vault.secrets` table is created by the Supabase Vault extension.
- 146 indexes; no invalid or not-ready indexes.
- 149 constraints, including one `NOT VALID` check constraint:
  `realtime.messages.messages_payload_exclusive`. Its presence is not evidence
  that restore introduced it; its source validity was not independently checked.
- 9 table triggers, 12 policies, and all 7 archived event triggers.
- 6 extensions (5 archived entries plus the default `plpgsql` extension).
- 6 migration-history rows, versions `20261007170000` through `20261019152707`.
- Exact row counts were queried. Non-empty tables were:
  `auth.flow_state` (3), `storage.migrations` (74),
  `auth.schema_migrations` (82), `realtime.schema_migrations` (91), and
  `supabase_migrations.schema_migrations` (6).
- All archived event-trigger functions and their owners were present. The
  `ensure_rls` function and event trigger are owned by `postgres`; the other six
  event-trigger functions and triggers are owned by `supabase_admin`.

These are restored-database observations. The archive does not provide
source-side row counts for comparison, so exact source-to-restore data
equivalence was not established.

## Not verified

- Supabase Auth, Storage, Realtime, or other service behavior.
- External Storage objects; database backups contain Storage metadata, not the
  underlying object files.
- Source-side table row counts or complete source-to-restore equivalence.
- Operational execution of event triggers; no DDL behavior test was performed.
- Complete per-object `pg_restore` output, which was not retained.
- The current integrity of the separate Google Drive copy.

## Preservation and repository state

The local source archive still has the recorded size and SHA-256. No existing
Supabase container or volume was stopped, restarted, deleted, or reset by this
drill. Both newly created restore-test containers and volumes are retained for
review and must not be removed without an explicit decision.

The repository branch is `fix/rls-auto-enable-fail-closed`. The changes that
were uncommitted when this report was first written — in
`scripts/supabase_migration_guard.py` and `tests/test_supabase_migration_guard.py` —
have since been committed as `92c8975090cc87d2216215174e06ae2b84c28eb8`
("Harden migration guard role and policy checks") and pushed to `origin`. This
report is now added and committed separately so it can be retrieved
independently of the local environment. No migration or hosted database
operation was performed by the restore drill.
