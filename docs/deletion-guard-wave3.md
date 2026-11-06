# Deletion Guard Wave 3

Wave 3 installs and verifies the fail-closed account revocation guard already defined by the repository's account-lifecycle design. It does not add a second lifecycle model for workspaces.

## State and gate

`public.account_lifecycle` is the authoritative per-account state. The installed migration defines `available`, `revocation_pending`, `revoked`, `purge_pending`, `purge_failed`, and `purged`; the trusted transition path permits the initial `available -> revocation_pending` denial transition. Missing or unreadable state fails closed.

For every authenticated `/v1` route, the Evidence Service performs a fresh Recovery Fence read and then a fresh account-lifecycle read using the verified user's JWT and the publishable Supabase key. Only `available` proceeds. Any other known state, an unknown state, an absent row, or a lifecycle read failure returns a denial (`403 Account unavailable` for a blocked/unknown state, `503 Account status is unavailable` for a read failure) before workspace resolution or data access. `/healthz` remains unauthenticated and available.

The gate covers the current service routes for memberships/personal workspace, workspace lookup, source listing/content, evidence list/create/read/update/delete, and relationship list/create/update/delete. Source content also rechecks the gate after the buffered read and before response delivery (`services/evidence-service/evidence_service/app.py:614-1065`). Existing BFF routes continue forwarding the user token; they do not bypass the service gate.

## Cancellation and scope limits

There is no cancel transition after revocation is accepted. A pending account remains denied until a separately authorized lifecycle transition; no user-facing reactivation or deletion-status route is added in this wave. The current account guard is principal/account scoped. A workspace-specific `workspace_lifecycle` table and workspace revocation transition are not present and are not invented here; workspace deletion remains out of scope.

This wave does not add account/workspace/source/snapshot/Storage purge, a deletion worker, `service_role` or `BYPASSRLS`, an independent external journal, or restore/PITR behavior. Wave 2's database-local deletion audit remains unchanged.

## Test evidence

`tests/test_evidence_service_account_lifecycle.py:265-377` exercises every current `/v1` route with `revocation_pending` and verifies a 403 occurs before directory, repository, catalog, or Storage calls. The same parameterized test covers the existing route surface; `test_status_is_not_cached_between_requests` verifies fresh state reads and available-to-revoked denial between requests.

The account-lifecycle migration is now included in the reviewed manifest, statement ledger, and local catalog expectations. `scripts/supabase_migration_guard.py validate`, `classify`, and the guard test suite pass locally. This is not production approval: the manifest explicitly records that human release review and a production migration plan remain separate controls.
