# Deletion migrations closure — 2026-11-07

Status: **STEP BLOCKED**. The official hosted production plan now passes after
the credential rotation, but the available GitHub credential cannot create the
`workflow_dispatch` event that the approved path requires to apply. No migration
was applied, no ad-hoc SQL was executed, and the Delete-account flag remains OFF.

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
| Plan digest used | `cabfe5c8c2e6edb441f92a74aaca629fe19c6f395e502e4e512a972e9bb66a56` (commit `723c4e1b3a07f49a36c94b7db2f6367bc817f9b0`) |
| Plan source | Official GitHub Actions `production-plan` run `42427516660`; artifact `supabase-plan-723c4e1…/plan.json` |
| Remote history at plan | 14 applied; the 7 reviewed migrations pending |
| Recovery mode | External R2 attestation: bucket `evidence-layer-db-backups`, object `dumps/2026-11-07/evidence-layer.dump`, 127,007 bytes, SHA-256 `6450cabf…32b73e`, non-production restore drill PASS, evidence `docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-07.md`. Attestation age at plan: ~1.9 h (valid; 48 h limit). Supabase PITR remains **off** with **0** managed backups. |

## Gate progression and exact failure

| Step | Result |
|---|---|
| `supabase_migration_guard.py validate` | **PASS** — manifest conforms to the reviewed inventory (21 files). |
| `supabase_migration_guard.py classify` | **PASS** — 435 statements, 362 registry-approved, 0 hard-denied. |
| Hosted `production-plan` — guard step | **PASS** — target, history, catalog, grants, and recovery verified. |
| Hosted `production-plan` — link / `migration list` / `db push --dry-run` | **PASS** — the rotated `Production` `SUPABASE_DB_PASSWORD` is accepted; no `28P01`. The run lists 14 applied and the same 7 pending migrations. |
| Hosted `production-plan` — artifact | **PASS** — digest `cabfe5c8…` published. |
| Hosted `production-apply` — trigger | **FAIL** — the approved path requires `workflow_dispatch` with `operation=apply`. The available GitHub credential receives HTTP **403** (`Resource not accessible by integration`) from the workflow-dispatch endpoint; the token has no Actions write permission. |
| Local fallback apply | **NOT RUN** — the mission requires the official workflow path and forbids ad-hoc SQL. The local Codespaces `SUPABASE_ACCESS_TOKEN` / `SUPABASE_DB_PASSWORD` are stale after the rotation. |

The workflow enablement from the prior run (PR #82/#83) is in place:
`production-plan` is read-only on `main`, and `production-apply` runs only on a
manual `workflow_dispatch` with `operation=apply` plus a typed project ref.

## Pending migrations (7)

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

## Post-checks (read-only; no apply occurred)

Performed against the deployed project's PostgREST endpoint with the public
publishable client key (not a secret):

| Check | Result |
|---|---|
| `public.account_lifecycle` | HTTP **404** `PGRST205` — still absent |
| `public.data_deletion_requests` | HTTP **404** `PGRST205` — still absent |
| `public.evidence_deletion_audit` | HTTP **404** `PGRST205` — still absent |
| `public.data_deletion_manifest` | HTTP **404** `PGRST205` — still absent |
| `public.data_deletion_work_set` | HTTP **404** `PGRST205` — still absent |
| `public.evidence_records` anonymous read | HTTP **401** `42501` — anonymous deny still holds |
| Applied migration count | 14 (unchanged) |

## Feature flag

`NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is **OFF** before and after this step.
The Delete-account panel is not rendered (`auth-account.tsx` enables it only when
the value is exactly `"true"`). No feature-flag configuration was changed.

## Residual risks

- The apply path is open only to a credential with GitHub Actions write (or the
  repository owner in the Actions UI); the current automation credential cannot
  dispatch it.
- The `Production` environment has **no protection rules** (no required
  reviewers, no main-only deployment branch policy).
- No PITR and no Supabase-managed backup; the external logical dump is a
  secondary safety net and the guard treats it as stale 48 h after upload.
- The logical dump excludes Auth, Storage, and extensions.
- The deployed Evidence Service still returns 404 for deletion routes.
- The flag must remain OFF until the schema is applied, the service is deployed,
  and an authenticated deletion smoke passes.

## Step outcome

**STEP BLOCKED — APPLY: NO — FLAG: OFF — ATTESTATION: valid — TABLES: FAIL.**

Exact gate: `production-apply` cannot be started because a `workflow_dispatch`
event requires Actions write permission, and the available GitHub credential
returns HTTP 403 (`Resource not accessible by integration`). The production plan
itself passes with the rotated database credential.

Next step: from the repository Actions UI (or a credential with Actions write),
run **Supabase migration validation and release** → *Run workflow* on `main` with
`operation=apply` and `confirm_project_ref=slxfsjnsrvfkdtxayiud`; then verify the
deletion tables, deploy the Evidence Service with deletion routes, and run an
authenticated delete smoke with the flag still OFF.
