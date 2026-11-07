# Deletion migrations closure — 2026-11-07

Status: **STEP BLOCKED**. The hosted production apply could not start because the
`Production` environment database credential is rejected. No migration was
applied, no ad-hoc SQL was executed, and the Delete-account flag remains OFF.

## Target

| Item | Value |
|---|---|
| Environment | Production |
| Project ref | `slxfsjnsrvfkdtxayiud` |
| Project name | `Evidence Layer` |
| Status | `ACTIVE_HEALTHY`, `eu-central-1` |

The ref matches the live app. No secret values are recorded here.

## Apply

**APPLY: NO.**

| Item | Result |
|---|---|
| Migrations applied | **none** |
| Plan digest used | `322ca806989feb1f2b1b703a9cc1d4ac5b11435f61604ee729551007cd41e709` (commit `8e7b72651a330912b9c8f76a6cd69e2e8374d175`) |
| Previously approved plan | `f657c90c193329d8a08350f59a5c0716bca1c73b07ab1154854f81c6bbf6afbc` (commit `8f676916d332223b81d6d888098d74764095e465`) |
| Difference between plans | Release plumbing only (PR #82/#83); the 21 migration files and their SHA-256 hashes are identical, so the 7 pending versions are unchanged. |
| Remote history | 14 applied; the same 7 pending after the attempt |
| Recovery mode | External R2 attestation (`supabase/recovery-attestation.json`): bucket `evidence-layer-db-backups`, object `dumps/2026-11-07/evidence-layer.dump`, 127,007 bytes, SHA-256 `6450cabf…32b73e`, non-production restore drill PASS. Supabase PITR is still **off** and there are **0** managed backups. Attestation age at the attempt: ~1.2 h (valid). |

## Gate progression and exact failure

| Step | Result |
|---|---|
| `supabase_migration_guard.py validate` | **PASS** — manifest conforms to the reviewed inventory (21 files). |
| `supabase_migration_guard.py classify` | **PASS** — 435 statements, 362 registry-approved, 0 hard-denied. |
| Local `plan` on production | **PASS** — 7 pending; target/history/catalog/grants pass; recovery satisfied by the external attestation. |
| PR #82 — enable reviewed plan and manual apply | Merged. Plan runs on `main`; apply requires `workflow_dispatch` with `operation=apply` and a typed project ref. A push to `main` never applies. |
| PR #83 — empty-ref fail-closed + reviewed default | Merged. The `Production` environment has no `SUPABASE_PROJECT_REF` variable and the available credential cannot create one (Actions variables/secrets API returns HTTP 403), so the workflow now falls back to the reviewed production ref. The guard now rejects an empty ref before any request. |
| Hosted `production-plan` — guard step | **PASS** — target, history, catalog, grants, and recovery verified in GitHub Actions. |
| Hosted `production-plan` — link/preview step | **FAIL** — the `Production` environment `SUPABASE_DB_PASSWORD` is rejected by Postgres: `FATAL: password authentication failed for user "postgres" (SQLSTATE 28P01)`. |
| Rotate the `Production` secret | **NOT POSSIBLE with the available credential** — writing environment secrets/variables returns HTTP 403 (`Resource not accessible by integration`). |

The credential that works from this workspace was not used for a local
`supabase db push`: the runbook designates GitHub Actions as the single
production migration mechanism, and the mission requires the official apply
path. No local push, direct DDL, migration repair, or restore was performed.

## Pending migrations (7)

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

## Post-checks (read-only; no apply occurred)

| Check | Result |
|---|---|
| `public.account_lifecycle` | absent (`pg_class` query; REST would be `PGRST205`) |
| `public.data_deletion_requests` | absent |
| `public.data_deletion_manifest` | absent |
| `public.data_deletion_work_set` | absent |
| `public.evidence_deletion_audit` | absent |
| Applied migration count | 14 (unchanged) |
| Anonymous evidence deny | not re-checked this run; no apply changed it |

## Feature flag

`NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is **OFF** before and after this step.
The Delete-account panel is not rendered.

## Residual risks

- The `Production` environment has **no protection rules** (no required
  reviewers, no main-only deployment branch policy); enabling the jobs exposes
  a manual apply path without a reviewer gate.
- The `Production` environment `SUPABASE_DB_PASSWORD` is stale/incorrect and must
  be rotated by a credential with Actions secret write access.
- No PITR and no Supabase-managed backup; the external logical dump is a
  secondary safety net and expires from the guard's view 48 h after upload.
- The logical dump excludes Auth, Storage, and extensions.
- The deployed Evidence Service still returns 404 for deletion routes.
- The flag must remain OFF until the schema is applied, the service is deployed,
  and an authenticated deletion smoke passes.

## Step outcome

**STEP BLOCKED — APPLY: NO — FLAG: OFF — ATTESTATION: valid — TABLES: unchanged/absent.**

Exact gate: `production-plan` fails at `supabase link`/`migration list` with
`FATAL: password authentication failed for user "postgres" (SQLSTATE 28P01)`
because the `Production` environment `SUPABASE_DB_PASSWORD` secret is wrong, and
the available credential cannot update it (HTTP 403).

Next step: rotate the `Production` environment `SUPABASE_DB_PASSWORD` (and add
`SUPABASE_PROJECT_REF` plus required reviewers), re-run `plan`, then dispatch
`apply`; afterwards deploy the Evidence Service with deletion routes and run an
authenticated delete smoke with the flag still OFF.
