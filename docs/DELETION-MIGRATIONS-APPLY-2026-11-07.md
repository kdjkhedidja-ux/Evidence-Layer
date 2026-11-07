# Deletion migrations apply — 2026-11-07

Status: **FAIL / NOT APPLIED.** The repository's official release path could not be used, and no ad-hoc SQL was executed. The Delete account feature flag remains OFF.

## Target analysis

| Target | Evidence | Usable now |
|---|---|---|
| `Evidence-test` (`ayrtyjgqlgtuiwuyigbz`) — documented production target of the release workflow and guard | `docs/SUPABASE_MIGRATION_RELEASE.md:1-8`; `scripts/supabase_migration_guard.py:39-40` | **NO** — the available token gets HTTP **404** for this project |
| Production `slxfsjnsrvfkdtxayiud` | Live app project | Not the reviewed target; release jobs are hard-disabled; no `SUPABASE_DB_PASSWORD` |
| Staging | `STAGING_DATABASE_URL` missing; the release doc states no staging target was verified (`docs/SUPABASE_MIGRATION_RELEASE.md:42-44`) | **NO** |

## Pending migrations

Production history contains **14** applied migrations. The following **7** reviewed migrations are pending:

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

## Guard results

| Command | Result |
|---|---|
| `supabase_migration_guard.py validate` | **PASS** — 21 files match the reviewed manifest |
| `supabase_migration_guard.py classify` | **PASS** — 435 statements, 362 registry-approved, 0 hard-denied |
| `supabase_migration_guard.py plan` (no target ref) | **FAIL** — "Configured project ref does not match the reviewed production target" |
| `supabase_migration_guard.py plan` with `SUPABASE_PROJECT_REF=ayrtyjgqlgtuiwuyigbz` | **FAIL** — "Supabase returned HTTP 404" |

The plan stops before the recovery-point check because the reviewed target is not reachable with the available credentials.

## Apply

**NOT RUN.** The documented mechanism is the GitHub Actions workflow `Supabase migration validation and release`:

- `production-plan` and `production-apply` are disabled by literal `&& false` conditions (`\.github/workflows/supabase-migrations.yml:57-60,84-87`).
- Enabling them requires the `Production` environment gates, `SUPABASE_PROJECT_REF`, `SUPABASE_ACCESS_TOKEN`, and `SUPABASE_DB_PASSWORD` (`docs/SUPABASE_MIGRATION_RELEASE.md:26-40`).
- The guard also requires a completed backup or PITR point newer than 48 hours (`scripts/supabase_migration_guard.py:1207-1222`); production reports `pitr_enabled: false` and zero backups.

No `supabase db push`, no direct database DDL, and no migration repair was executed.

## Post-apply checks

Production was only observed read-only:

| Check | Result |
|---|---|
| `evidence_records` (existing table) | HTTP **401/42501** — present, anonymous denied |
| `account_lifecycle` | HTTP **404 PGRST205** — absent |
| `data_deletion_requests` | HTTP **404 PGRST205** — absent |
| `evidence_deletion_audit` | HTTP **404 PGRST205** — absent |

## Feature flag

`NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is still unset/off. The Delete account panel is not rendered.

## Limits and safety

- No secret, connection string, or token appears in this document.
- No production restore, worker deployment, or feature-flag change was made.
- The verified logical dump from 2026-11-07 remains available in R2 as a secondary safety net.

## Next recommended step

1. Restore access to the reviewed target or designate a new one: provide `SUPABASE_PROJECT_REF`, `SUPABASE_ACCESS_TOKEN`, and `SUPABASE_DB_PASSWORD` for the intended staging/production project.
2. Verify a recovery point newer than 48 hours (backup or PITR).
3. Enable the `production-plan`/`production-apply` jobs after the `Production` environment gates are configured.
4. Run `plan`, review the digest and dry-run, then approve `apply`.
5. Only after the apply: deploy the Evidence Service with the deletion routes, run the authenticated smoke, and then enable the delete feature flag.
