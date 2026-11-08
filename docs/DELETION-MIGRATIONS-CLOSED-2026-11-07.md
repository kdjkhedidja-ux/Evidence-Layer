# Deletion migrations closure — 2026-11-07

Status: **STEP CLOSED**. The seven deletion/lifecycle migrations were applied to
production through the official GitHub Actions release workflow. The
Delete-account flag remains OFF.

## Target

| Item | Value |
|---|---|
| Environment | Production |
| Project ref | `slxfsjnsrvfkdtxayiud` |
| Project name | `Evidence Layer` |
| Status | `ACTIVE_HEALTHY`, `eu-central-1` |

The ref matches the live app. No secret values are recorded here.

## Apply

**APPLY: YES.**

| Item | Result |
|---|---|
| Release run | GitHub Actions `42429182059` (`workflow_dispatch`, `operation=apply`, `confirm_project_ref` matched) |
| Plan digest used | `6d229cd06eb1d1d80c4c4e38e01c72243e1df810960d1da61940b2a75f45d8e2` (commit `266f8fe08d703f732dff0801371d724d83dc9dac`) |
| Apply step | **PASS** — `supabase db push` applied all seven migrations in order |
| Remote history | **21 applied / 0 pending** |
| Recovery mode | External R2 attestation (`supabase/recovery-attestation.json`): bucket `evidence-layer-db-backups`, object `dumps/2026-11-07/evidence-layer.dump`, 127,007 bytes, SHA-256 `6450cabf…32b73e`, non-production restore drill PASS. Attestation age at apply: ~2 h (valid; 48 h limit). Supabase PITR remains **off** with **0** managed backups. |

## Migrations applied (7)

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

## Post-apply verification

The workflow's apply step succeeded, but its post-apply guard assertion first
failed on **stale expected inventory**, not on the database state:

- `20261105140000` intentionally drops the 85-character unique constraint and
  replaces it with `data_deletion_work_set_request_target_key`; the guard still
  expected the dropped index.
- The account-lifecycle trigger lives in `auth`, which the trigger catalog query
  did not read.
- The three account roles and their documented one-SET-edge memberships were not
  in the roles/membership catalog scope.

The guard was corrected to model those reviewed migrations. Re-running the
official verification with the apply run's exact plan artifact now reports:

| Check | Result |
|---|---|
| `verify` (history, catalog, grants) | **PASS** — 21 migrations; pending=0 |
| `plan` on the applied state | **PASS** — 21 applied / 0 pending |
| `validate` / `classify` | **PASS** — 21 files; 435 statements, 362 registry-approved, 0 hard-denied |

## Post-checks (read-only)

Performed against the deployed project's PostgREST endpoint with the public
publishable client key (not a secret):

| Check | Result |
|---|---|
| `public.account_lifecycle` | HTTP **401** — exists; no longer `PGRST205` |
| `public.data_deletion_requests` | HTTP **401** — exists; no longer `PGRST205` |
| `public.evidence_deletion_audit` | HTTP **401** — exists; no longer `PGRST205` |
| `public.data_deletion_manifest` | HTTP **401** — exists; no longer `PGRST205` |
| `public.data_deletion_work_set` | HTTP **401** — exists; no longer `PGRST205` |
| `public.evidence_records` anonymous read | HTTP **401** `42501` — anonymous deny still holds |

## Feature flag

`NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is **OFF** before and after the apply.
The Delete-account panel is not rendered (`auth-account.tsx` enables it only when
the value is exactly `"true"`). No feature-flag configuration was changed.

## Residual risks

- The deployed Evidence Service still returns 404 for deletion routes; it must be
  redeployed with the deletion endpoints before any delete smoke.
- The Delete-account flag must remain OFF until that deployment and an
  authenticated deletion smoke pass.
- No PITR and no Supabase-managed backup; the external logical dump is a
  secondary safety net and the guard treats it as stale 48 h after upload.
- The logical dump excludes Auth, Storage, and extensions.
- The `Production` environment has **no protection rules** (no required
  reviewers, no main-only deployment branch policy).
- The guard expectation fixes are part of the accompanying code change and are
  validated by the post-apply `verify` and `plan` runs above.

## Step outcome

**STEP CLOSED — APPLY: YES — FLAG: OFF — ATTESTATION: valid — TABLES: OK.**

Next step: deploy the Evidence Service with the deletion routes, then run the
authenticated delete smoke with `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` still OFF.
