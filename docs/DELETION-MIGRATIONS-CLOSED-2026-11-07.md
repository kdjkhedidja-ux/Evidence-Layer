# Deletion migrations closure — 2026-11-07

Status: **STEP BLOCKED**. The official release path stopped at `plan`. No migration was applied, no ad-hoc SQL was executed, and the Delete-account flag remains OFF.

## Target

| Item | Value |
|---|---|
| Environment | Production |
| Project ref | `slxfsjnsrvfkdtxayiud` |
| Project name | `Evidence Layer` |
| Status | `ACTIVE_HEALTHY`, `eu-central-1` |

The ref matches the live app. No secret values are recorded here.

## Preconditions

| Check | Result |
|---|---|
| `SUPABASE_PROJECT_REF` | Set to the production ref; project readable with the access token. |
| `SUPABASE_ACCESS_TOKEN` | Set; can read the production project. |
| `SUPABASE_DB_PASSWORD` | Set (value not printed). |
| R2 rollback dump | Present: `dumps/2026-11-07/evidence-layer.dump`, 127,007 bytes, SHA-256 metadata recorded. |
| Delete-account flag | **OFF**: `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is unset. |
| Release jobs | **Disabled** by literal `&& false` (`\.github/workflows/supabase-migrations.yml:58,100`). |
| Recovery point < 48 h | **Missing**: `pitr_enabled: false`, `backups: 0`, `physical_backup_data: 0`. |

## Guard results

| Command | Result |
|---|---|
| `supabase_migration_guard.py validate` | **PASS** — manifest conforms to the reviewed inventory (21 files). |
| `supabase_migration_guard.py classify` | **PASS** — 435 statements, 362 registry-approved, 0 hard-denied. |
| `supabase_migration_guard.py plan` | **FAIL** — "No recent completed backup or PITR recovery point verified". The plan now reaches production; target, history, catalog, and grant checks pass. |

The guard was retargeted from `ayrtyjgqlgtuiwuyigbz` / `Evidence-test` (HTTP 404) to production `slxfsjnsrvfkdtxayiud` / `Evidence Layer`, with `SUPABASE_PROJECT_REF` still able to override an explicitly reviewed environment. `plan` now reaches the project and passes the target, history, catalog, and grant checks.

One guard rule was corrected during the retarget: management read-only queries run as `supabase_read_only_user`, so the membership audit now recognises exactly one admin membership held by the project owner `postgres`, while still requiring `admin_option`, no `SET`, and no `INHERIT`.

The remaining plan gate requires a completed backup or PITR point newer than 48 hours (`scripts/supabase_migration_guard.py:1207-1222,1233-1234`). Production has neither.

## Apply

**NOT RUN.** No `production-plan`/`production-apply` workflow execution, no `supabase db push`, no direct DDL. The release jobs are disabled and the plan did not pass.

## Pending migrations (7)

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

Production history contains 14 applied migrations; these seven are absent.

## Post-checks (read-only, unchanged)

| Object | Result |
|---|---|
| `public.account_lifecycle` | `null` — absent |
| `public.data_deletion_requests` | `null` — absent |
| `public.data_deletion_manifest` | `null` — absent |
| `public.evidence_deletion_audit` | `null` — absent |
| `public.data_deletion_work_set` | `null` — absent |
| `public.evidence_records` (existing) | present; anonymous SELECT denied |

## Feature flag

`NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is **OFF** before and after this step. The Delete-account panel is not rendered.

## Residual risks

- No PITR and no Supabase-managed backup; the logical dump is a secondary safety net only.
- The logical dump excludes Auth, Storage, and extensions.
- The deployed Evidence Service still returns 404 for deletion routes.
- The flag must remain OFF until the schema is applied, the service is deployed, and an authenticated deletion smoke passes.

## Step outcome

**STEP BLOCKED** by the recovery gate:

1. `plan` now reaches production and passes target/history/catalog/grants, but fails with "No recent completed backup or PITR recovery point verified".
2. Production has `pitr_enabled: false` and zero backups, so no recovery point within 48 hours exists.

The release jobs remain disabled until that gate and the environment controls are satisfied.

Release jobs remain disabled, as required by the runbook until the recovery gate and environment controls are satisfied.

## Recommended next step

1. Enable a recovery point (PITR or completed backup) and verify it is newer than 48 hours.
2. Configure/verify the `Production` environment gates, then enable `production-plan`/`production-apply`.
3. Run `plan`, review the digest and dry-run, then approve `apply`.
4. Deploy the Evidence Service with the deletion routes and run the authenticated delete smoke — with the flag still OFF until that smoke passes.
