# Production restore verification — 2026-11-27

Status: **B2 UPLOAD + DISPOSABLE RESTORE DRILL PASS.**
No production restore, migration, or worker deployment was executed by this
run. It refreshes the recovery point required by the migration guard before
applying, in order, `20261127100000_user_usage_caps.sql` (#264),
`20261127110000_connector_stale_evidence_audit_actor.sql` (#268) and
`20261127120000_evidence_search_prefilter.sql` (#269).

## Backup artifact

| Item | Value |
|---|---|
| Source | Production Supabase project `slxfsjnsrvfkdtxayiud` (read-only `pg_dump` through the verified pooler) |
| Taken at | 2026-11-27T00:09:09Z, production at migration `20261126140000` |
| Tool | `scripts/restore_drill.py --keep` (PostgreSQL 17 client) |
| Format | PostgreSQL custom dump |
| Schemas | `public`, `private` |
| Provider | Backblaze B2 |
| Bucket | `evidence2` |
| Object key | `dumps/2026-11-27/evidence-layer.dump` |
| Endpoint | `https://s3.eu-central-003.backblazeb2.com` |
| SHA-256 | `0e004d64ad24d945f58b273738a95980854015b6ebfac55fef7a5e43c07ab38f` |
| Size | 3,742,427 bytes |
| Uploaded at | 2026-11-27T00:09:57Z |
| Checksum object | `dumps/2026-11-27/evidence-layer.dump.sha256` |
| Upload check | Object re-downloaded; SHA-256 and size match |

The bucket is private: an unauthenticated `GET` of the object returned HTTP
401. The dump is about four times the 2026-11-26 one (925,365 bytes) because
the GitHub connector has since indexed the repository (778 evidence records,
was 57).

## Restore target

- Disposable PostgreSQL 17 container, non-production, removed after the drill.
- Production non-system roles and `auth`, `extensions`, and `storage` stubs
  were created because the dump excludes those schemas.
- Restored with `pg_restore --no-owner --no-privileges` in `pre-data`, `data`,
  and `post-data` sections; Auth FK stubs were inserted between `data` and
  `post-data`.
- The only restore error was the expected `CREATE SCHEMA public` precondition.

## Restore results

| Check | Result |
|---|---|
| Restore process | PASS (one non-blocking `public` schema precondition) |
| `workspaces` | 127, matches source |
| `evidence_records` | 778, matches source |
| `data_deletion_requests` | 51, matches source |
| `evidence_deletion_audit` | 563, matches source |
| `workspace_memberships` | 128, matches source |
| Deletion reconciliation verifier | `status=clean`, `requests_checked=42`, `failures=[]`, checked at 2026-11-27T00:09:28Z |

## Limits and non-claims

- `auth`, `storage`, and `extensions` are outside the dump. The drill used
  stubs; it is not an Auth or Storage restore proof.
- PITR remains disabled and no provider-managed backup is listed.
- No production restore was attempted.
- The verifier proves only that the restored rows do not resurrect terminal
  `purged` account data. It does not prove provider Storage/Auth absence.
- This is a rollback aid for the restored schema/data subset, not full
  disaster recovery.

## Follow-up

1. Keep the B2 object for at least 14 days.
2. The attestation expires 48 hours after upload (2026-11-29T00:09:57Z); apply
   the pending migrations before then or refresh the dump and drill.
3. Decide on provider PITR/managed backup (open since 2026-11-15).
4. Re-run `python -m evidence_service.restore_reconciliation` after any future
   restore or schema migration.
5. The local working copy of the dump in the Codespace is deleted after the
   migrations are applied.
