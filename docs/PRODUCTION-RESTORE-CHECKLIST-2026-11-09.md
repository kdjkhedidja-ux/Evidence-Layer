# Production restore verification — 2026-11-09

Status: **LOGICAL BACKUP + RESTORE DRILL PASS**. A fresh production dump was uploaded to the private R2 bucket and restored into a disposable PostgreSQL 17 instance. No production restore was executed.

## Production project

| Item | Value |
|---|---|
| Project | `Evidence Layer` (`slxfsjnsrvfkdtxayiud`) |
| Status | `ACTIVE_HEALTHY`, `eu-central-1` |
| `pitr_enabled` | **false** |
| API-listed backups | **0** |
| Connection used | IPv4 Supavisor pooler, session mode (`aws-1-eu-central-1.pooler.supabase.com:5432`) |

Direct `db.<ref>.supabase.co:5432` is IPv6-only and unreachable from this Codespace; the pooler was required.

## Backup artifact

| Item | Value |
|---|---|
| Scope | schemas `public` + `private` (no `auth`, `storage`, `extensions`) |
| Format | PostgreSQL custom dump |
| Size | 321,535 bytes |
| SHA-256 | `8d769914880eadaaf7c38882db1e5c65050b8fb9d20dc0b18f49f865a0fe0b99` |
| Object key | `dumps/2026-11-09/evidence-layer.dump` |
| Checksum object | `dumps/2026-11-09/evidence-layer.dump.sha256` |
| Bucket | `evidence-layer-db-backups` (private; `r2.dev` disabled, 0 custom domains) |
| Uploaded at | `2026-11-09T23:30:14Z` |
| Read-back verification | downloaded object SHA-256 matches the recorded digest |

The local dump and temporary SQL/plain-text expansion were deleted after the drill; the drill container was removed.

## Restore drill

Target: disposable `postgres:17-alpine` container, no production data attached.

Preparation: created the roles referenced by policies and grants (`anon`, `authenticated`, `service_role`, snapshot verifier/publisher/allowlist/audit roles, `rls_enabled`, lifecycle worker roles), an `auth` schema with a `auth.uid()` stub, and an `auth.users` stub populated from the 25 user IDs referenced by restored rows. Restore used `pg_restore --no-owner --no-privileges` into an empty database with the default `public` schema removed first.

Result:

| Check | Result |
|---|---|
| `pg_restore` exit / errors | **0 errors** |
| Tables (public + private) | 18 |
| Policies restored | 72 |
| FKs to `auth.users` | 12 / 12 validated |
| Orphan personal owners | 0 |
| Row counts | all 18 tables match the source |

| Table | Rows (source = restored) |
|---|---|
| `public.workspaces` | 15 |
| `public.workspace_memberships` | 16 |
| `public.evidence_records` | 8 |
| `public.evidence_relationships` | 0 |
| `public.source_snapshots` | 0 |
| `public.source_grants` | 0 |
| `public.accounts` | 25 |
| `public.organizations` | 2 |
| `public.organization_memberships` | 4 |
| `public.account_lifecycle` | 25 |
| `public.data_deletion_requests` | 25 |
| `public.data_deletion_manifest` | 17 |
| `public.data_deletion_storage_queue` | 0 |
| `public.data_deletion_work_set` | 5 |
| `public.evidence_deletion_audit` | 213 |
| `private.snapshot_verifier_allowlist` | 0 |
| `private.snapshot_verifier_allowlist_audit` | 0 |
| `private.snapshot_verifier_read_policy_baseline` | 6 |

## Limits

- `auth`, `storage`, and `extensions` schemas are outside the dump. Authentication identities and Storage objects are not covered by this artifact; Supabase's own backups remain required for those.
- The drill used an `auth.users` stub because the dump intentionally excludes Auth. FK validation is meaningful for the restored schema, not proof of Auth restore.
- PITR is still disabled and no scheduled backup is listed by the API. This is a logical backup, not point-in-time recovery.
- No production restore was attempted.

## Decision

**GO for repository validation and non-destructive release work** from a backup standpoint:

- a fresh production dump exists with a recorded, read-back-verified checksum;
- a clean non-production restore reproduced all row counts, 72 policies, and 12 validated Auth FKs.

Conditions:

1. Keep the dump for at least 14 days.
2. Apply any schema migration through the release process; do not hand-apply.
3. Broader disaster recovery remains partial while PITR is disabled and Auth/Storage are outside the dump.
