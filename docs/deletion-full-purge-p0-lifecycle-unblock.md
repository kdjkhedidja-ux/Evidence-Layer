# Full Purge P0: Account Lifecycle Review Unblock

Status date: 2026-11-05. This P0 makes the existing account lifecycle migration locally reviewable and preserves the fail-closed service gate. It does not implement purge execution, a deletion worker, Storage deletion, published-snapshot deletion, production PITR, JEV, billing, or connectors.

## Why the guard blocked

The repository guard had three independent review failures:

1. `supabase/migrations/20261102120000_account_lifecycle.sql` was present in the migration directory but had been treated as an unreviewed working-tree migration. The manifest/ledger process requires every present migration to be listed with an exact SHA-256 and every registry-tier statement to have an ordered rationale entry (`scripts/supabase_migration_guard.py:100-116,187-207`).
2. The lifecycle migration contained `BEGIN`/`COMMIT`, `LOCK TABLE`, `NOTIFY`, and `SET ROLE` statements. The SQL lexer reports the transaction/lock/notify forms as unrecognised and the guard hard-denies session role switches. These were review blockers rather than lifecycle behavior requirements (`scripts/supabase_migration_guard.py:119-156`; previous lifecycle statements at `20261102120000_account_lifecycle.sql:6,223-253,320-425`).
3. The guard's catalog model did not know about the lifecycle and Wave 2 audit objects. As a result, expected tables, indexes, policies, triggers, functions, roles, and foreign keys omitted objects created by the two migrations; the guard tests reported catalog completeness failures. Catalog expectations are generated in `scripts/supabase_migration_guard.py:446-802`, and the guard tests pin the reviewed statement/file counts in `tests/test_supabase_migration_guard.py:696-710,825-827`.

## Changes made

- Removed only migration-guard-incompatible transaction wrappers, session role switches, lock/notify statements from `20261102120000_account_lifecycle.sql`.
- Preserved the dedicated `account_lifecycle_provisioner` trust boundary by making the Auth provisioning trigger function `SECURITY DEFINER` and transferring its ownership to that NOLOGIN, NOINHERIT, NOBYPASSRLS role. The migration still performs role-safety, privilege, RLS, and policy-count assertions (`20261102120000_account_lifecycle.sql:22-41,150-245,320-411`).
- Updated exact migration hashes and provenance in `supabase/migrations.manifest.json` for the reviewed lifecycle and audit migration bytes.
- Added lifecycle/audit registry statements and explicit rationale references to `supabase/migrations.statements.json`. Only registry-tier statements are ledger-approved; non-registry statements are not silently treated as approvals.
- Extended `scripts/supabase_migration_guard.py` catalog expectations for lifecycle/audit tables, indexes, policies, triggers, functions, roles, and foreign keys, including policy-to-relation identity mapping.
- Updated guard test numeric confirmations for the current 16-file, 282-statement, 228-registry-approved local inventory (`tests/test_supabase_migration_guard.py:519,698-710,827`).

## Lifecycle and gate confirmation

The migration state constraint and service gate use the same blocked-state contract:

- `available` permits protected access.
- `revocation_pending`, `revoked`, `purge_pending`, `purge_failed`, and `purged` are denied.
- Absent, malformed, unknown, or unreadable lifecycle state fails closed.

The service performs the account lifecycle read after the Recovery Fence and before protected route work (`services/evidence-service/evidence_service/app.py:243-289`). Current route coverage includes `/v1/me`, personal-workspace provisioning, workspace lookup, source listing/content, evidence list/create/read/update/delete, and relationship list/create/update/delete; source content rechecks before delivery (`app.py:614-1065`). The route tests assert 403 and zero downstream directory/repository/catalog/Storage calls for `revocation_pending` (`tests/test_evidence_service_account_lifecycle.py:265-397`). No service-role credential is introduced on user paths.

## Local validation

The following checks pass locally without applying migrations to production:

- `PYTHONPATH=. python scripts/supabase_migration_guard.py validate`
- `PYTHONPATH=. python scripts/supabase_migration_guard.py classify`
- `PYTHONPATH=. python tests/test_supabase_migration_guard.py` (71 tests passed)
- Python compilation of the lifecycle service and test modules

The lifecycle pytest module remains environment-blocked when run in the current interpreter because `fastapi` is not installed (`ModuleNotFoundError: No module named 'fastapi'`). This is a test-environment limitation, not a passing runtime result. No local or production migration apply was performed.

## Human-only and P1 blockers

Local reviewability is **PASS**. Human production approval is **NOT DONE**: the manifest note explicitly says byte identity and local static review do not prove migration safety or authorize deployment. A reviewer must approve the migration semantics, deployment plan, remote preflight, and rollback/recovery procedure.

P1 remains blocked/deferred by:

- Workspace-specific lifecycle state and transitions.
- Deletion request/approval/immutable target manifest and external journal durability.
- Purge worker privilege design and execution.
- Source, Storage, and published-snapshot purge semantics.
- Provider-backed PITR/restore reconciliation tied to deletion history.
- Installation of the Python test dependencies and execution of the lifecycle HTTP suite in a controlled local environment.
