# Deletion-worker lease production status — 2026-11-15

Status: **CLOSED. Migration 47 applied and verified; the worker was redeployed;
all four acceptance smokes passed; the queue is healthy.**

## Migration apply

The GitHub `production-plan` job passed on `main` (`285f4d9`) with the B2
recovery attestation. The `production-apply` dispatch was unavailable because
the available GitHub token lacks `actions:write`. The same reviewed plan was
applied through the documented plan gates:

1. `assert-plan` passed for digest
   `4e86da4d669b6721dcc821190332db6c54966536fb806ce4f34d82ee5c96e9ff`.
2. `supabase db push --dry-run` listed only
   `20261114230000_deletion_worker_lease_retry.sql`.
3. `supabase db push --db-url` applied that one migration through the verified
   session-mode pooler.
4. `supabase_migration_guard.py verify` reported **47 migrations, pending=0**.

## Worker redeploy

- Railway project: `evidence-layer-github-test`, production environment.
- Service: `evidence-deletion-worker`.
- Deployment: `736cc0f5-495d-4ef3-8376-a4119e142fdb`, commit `9e4329e`, status
  `SUCCESS`.
- Variables set without a deploy first:
  - `DELETION_WORKER_LEASE_SECONDS=300`
  - `DELETION_MAX_ATTEMPTS=5`
  - `DELETION_WORKER_SINGLE_INSTANCE=true`
- The worker remains at one replica.

## Acceptance smokes

| Smoke | Result |
|---|---|
| accepted -> purged | `purged`, `attempts=1` |
| purge_failed -> retry -> purged | `purge_failed` at attempt 1, then `purged` at attempt 2 |
| lease-expiry recovery | expired `accepted` lease reclaimed, then `purged` at attempt 2 |
| alerts | healthcheck exit 2 with `purge_failed_retry_overdue` and `stale_lease` |
| healthy queue after cleanup | healthcheck exit 0, `status=ok`, all metrics zero |

The retry and lease assertions prove the new claim path is active in production;
a pre-lease worker would have left `attempts=0`.

## Residual cleanup

- All synthetic Auth users and deletion requests were removed.
- All synthetic work-set and manifest rows were removed.
- Thirty-two content-free `evidence_deletion_audit` rows remain because that
  table is append-only by design. No customer data was involved.
- Final production checks: 47 migrations, pending=0; no accepted or
  `purge_pending` requests; healthcheck `ok`.

## Follow-up

- Rotate the worker's privileged credentials by **2026-12-11**.
- Keep one replica until horizontal scaling is explicitly validated; then the
  advisory lock may be disabled deliberately.
- Refresh the B2 recovery dump before the 48-hour attestation gate expires.
