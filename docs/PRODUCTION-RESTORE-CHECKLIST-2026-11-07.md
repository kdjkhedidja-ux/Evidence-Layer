# Production restore verification — 2026-11-07

Status: **LOGICAL BACKUP + RESTORE DRILL PASS**. A real production dump was uploaded to the private R2 bucket and restored into a disposable PostgreSQL 17 instance. No production restore was executed.

## Production project

| Item | Value |
|---|---|
| Project | `Evidence Layer` (`slxfsjnsrvfkdtxayiud`) |
| Status | `ACTIVE_HEALTHY`, `eu-central-1` |
| Database size | 14 MB |
| `pitr_enabled` | **false** |
| API-listed backups | **0** |
| Connection used | IPv4 Supavisor pooler, session mode |

Direct `db.<ref>.supabase.co:5432` is IPv6-only and unreachable from this Codespace; the pooler was required.

## Backup artifact

| Item | Value |
|---|---|
| Scope | schemas `public` + `private` (no `auth`, `storage`, `extensions`) |
| Format | PostgreSQL custom dump |
| Size | 127,007 bytes |
| SHA-256 | `6450cabff82f64a09a28616ae4d5a2284575bedf2bb869e9b24867732d32b73e` |
| Object key | `dumps/2026-11-07/evidence-layer.dump` |
| Checksum object | `dumps/2026-11-07/evidence-layer.dump.sha256` |
| Bucket | `evidence-layer-db-backups` (private; `r2.dev` disabled, 0 custom domains) |

The local dump was deleted after upload; the drill container was removed.

## Restore drill

Target: disposable `postgres:17-alpine`, no production data attached.

Preparation: create roles referenced by policies (`anon`, `authenticated`, `service_role`, `snapshot_publisher_owner`, `snapshot_verifier`, allowlist manager/owner/audit, `rls_enabled`, lifecycle worker roles), `auth.uid()` stub, and `auth.users` stub populated with the owner IDs referenced by the restored rows.

Result:

| Check | Result |
|---|---|
| `pg_restore` exit / errors | **0 errors** |
| Tables (public + private) | 9 |
| Policies restored | 32 |
| FKs to `auth.users` validated | 8 |
| `workspaces` rows | 2 (matches source) |
| `workspace_memberships` rows | 2 (matches source) |
| `evidence_records` | 0 (matches source) |
| `evidence_relationships` | 0 (matches source) |
| `source_snapshots` / `source_grants` | 0 / 0 (matches source) |

## Limits

- `auth`, `storage`, and `extensions` schemas are outside the dump. Authentication identities and Storage objects are not covered by this artifact; Supabase's own backups remain required for those.
- The drill used an `auth.users` stub because the dump intentionally excludes Auth. FK validation is meaningful for the restored schema, not proof of Auth restore.
- PITR is still disabled and no scheduled backup is listed by the API. This is a logical backup, not point-in-time recovery.
- No production restore was attempted.

## Decision

**GO for the deletion schema migration apply** from a backup standpoint:

- live authenticated smoke: PASS (`/healthz`, `/v1/me`, evidence list);
- verified logical dump exists with a recorded checksum;
- a clean non-production restore reproduced the row counts, policies, and FK validation.

Conditions:

1. Keep the dump for at least 14 days.
2. Apply during a maintenance window through the release process; do not hand-apply.
3. The **Delete account** feature flag stays off until the Evidence Service with the deletion routes is deployed and an authenticated deletion smoke passes.
4. Broader disaster recovery remains partial while PITR is disabled and Auth/Storage are outside the dump.
