# Production restore verification — 2026-11-11

Status: **LOGICAL BACKUP + RESTORE DRILL PASS**, with one non-blocking schema precondition.

A fresh production logical dump was uploaded to the private R2 backup bucket and restored into a disposable PostgreSQL 17 instance. No production restore was executed.

## Backup artifact

| Item | Value |
|---|---|
| Source | Production Supabase project (read-only `pg_dump`) |
| Format | PostgreSQL custom dump |
| Schemas | `public`, `private` |
| Object key | `dumps/2026-11-11/evidence-layer.dump` |
| SHA-256 | `00c8ca2b5391a573e9907167f5ab9205fa6f473934acce9bd836dbde8c1ade80` |
| Size | 400,005 bytes |
| Checksum object | `dumps/2026-11-11/evidence-layer.dump.sha256` |
| Upload target | Private R2 bucket `evidence-layer-db-backups` |

The local dump and temporary restore container were removed after the drill.

## Restore target

- Disposable PostgreSQL 17 container, non-production.
- Created the production non-system roles and an `auth` schema with an `auth.users` FK stub plus `auth.uid()` stub.
- Restored with `pg_restore --no-owner --no-privileges`.
- The default empty `public` schema was present; `pg_restore` emitted one non-blocking `CREATE SCHEMA public` precondition error. All tables, policies, and data then restored.

## Restore results

| Check | Result |
|---|---|
| Restore process | Completed; one non-blocking public-schema precondition error |
| Tables restored (`public` + `private`) | 24 |
| Policies restored | 88 (78 public, 10 private) |
| Auth FK stub users | 24 |
| `public.workspaces` rows | 14 |
| `public.evidence_records` rows | 7 |
| `public.data_deletion_requests` rows | 31 |
| `public.evidence_deletion_audit` rows | 276 |
| `public.data_export_jobs` rows | 0 |

## Limits and non-claims

- `auth`, `storage`, and `extensions` schemas are outside the dump. Supabase Auth and Storage are not covered by this artifact.
- The `auth.users` stub validates FK shape only; it is not an Auth restore.
- Connector migration 41 was not applied to production at dump time, so `connector_connections`/`connector_documents` are not in this dump.
- PITR remains disabled and provider-managed backups remain unproven.
- No production restore was attempted.
- This drill is a rollback aid for the restored schema/data subset, not a full disaster-recovery proof.

## Follow-up

1. Keep the dump for at least 14 days.
2. Refresh before the 48-hour attestation gate expires.
3. Re-run the drill after connector migration 41 is applied and after any future Auth/Storage restore work.
4. Decide on provider PITR/managed backup by 2026-11-30.
