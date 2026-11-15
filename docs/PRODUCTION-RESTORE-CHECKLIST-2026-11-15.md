# Production restore verification — 2026-11-15

Status: **B2 UPLOAD + DISPOSABLE RESTORE DRILL PASS.**
No production restore, migration, or worker deployment was executed.

## Backup artifact

| Item | Value |
|---|---|
| Source | Production Supabase project (read-only `pg_dump` through the verified pooler) |
| Format | PostgreSQL custom dump |
| Schemas | `public`, `private` |
| Provider | Backblaze B2 |
| Bucket | `evidence2` |
| Object key | `dumps/2026-11-15/evidence-layer.dump` |
| Endpoint | `https://s3.eu-central-003.backblazeb2.com` |
| SHA-256 | `61df647210bef7d34ff082ffa23c3e892043d15d2949f8a450fd3f2d6b43db6a` |
| Size | 768,160 bytes |
| Uploaded at | 2026-11-15T00:08:06Z |
| Checksum object | `dumps/2026-11-15/evidence-layer.dump.sha256` |

The bucket is private: an unauthenticated S3 `GET` returned HTTP 401.

## Restore target

- Disposable PostgreSQL 17 container, non-production.
- Created the production non-system roles and `auth`, `extensions`, and
  `storage` stubs required by the dump (the dump excludes those schemas).
- Restored with `pg_restore --no-owner --no-privileges` in `pre-data`, `data`,
  and `post-data` sections; Auth FK stubs were inserted between `data` and
  `post-data`.
- The only restore error was the expected `CREATE SCHEMA public` precondition.

## Restore results

| Check | Result |
|---|---|
| Restore process | PASS (one non-blocking `public` schema precondition) |
| `workspaces` | 13, matches source |
| `evidence_records` | 56, matches source |
| `data_deletion_requests` | 51, matches source |
| `evidence_deletion_audit` | 529, matches source |
| `workspace_memberships` | 13, matches source |
| `auth.users` FKs | 18 |
| Policies (`public` + `private`) | 98 |
| Deletion reconciliation verifier | `status=clean`, `requests_checked=42`, `failures=[]` |

## Limits and non-claims

- `auth`, `storage`, and `extensions` are outside the dump. The drill used
  stubs; it is not an Auth or Storage restore proof.
- PITR remains disabled and no provider-managed backup is listed.
- No production restore was attempted.
- The verifier proves only that the restored database rows do not resurrect
  terminal `purged` account data. It does not prove provider Storage/Auth
  absence.
- This is a rollback aid for the restored schema/data subset, not full disaster
  recovery.

## Follow-up

1. Keep the B2 object for at least 14 days.
2. Refresh the dump and rerun the drill before the 48-hour attestation gate
   expires.
3. Decide on provider PITR/managed backup by 2026-11-30.
4. Re-run `python -m evidence_service.restore_reconciliation` after any future
   restore or schema migration.
