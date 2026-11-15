# Deletion-worker lease production status — 2026-11-15

Status: **migration 47 applied and verified; worker still runs the pre-lease
build; redeploy and acceptance smokes pending.**

## Migration apply

The GitHub `production-plan` job passed on `main` (`285f4d9`) with the B2
recovery attestation. The `production-apply` dispatch was unavailable because
the available GitHub token lacks `actions:write`. The same reviewed plan was
then applied through the documented plan gates:

1. `assert-plan` passed for digest
   `4e86da4d669b6721dcc821190332db6c54966536fb806ce4f34d82ee5c96e9ff`.
2. `supabase db push --dry-run` listed only
   `20261114230000_deletion_worker_lease_retry.sql`.
3. `supabase db push --db-url` applied that one migration through the verified
   session-mode pooler.
4. `supabase_migration_guard.py verify` reported **47 migrations, pending=0**.

This was an operator-authorized manual apply after the reviewed plan and fresh
restore drill; it is not the normal GitHub apply path.

## Production smoke

A synthetic account and accepted deletion request were created, then processed
by the deployed worker:

```text
accepted -> purge_pending -> purged  (about 45 seconds)
```

Evidence on the request:

- `attempts = 0`
- `lease_owner` empty
- no lease row

That means the deployed worker still uses the pre-lease build. The new claim
path (`attempts` increment, lease ownership) was not exercised in production.
The worker is running, but it is not the new build.

The synthetic Auth row was inserted directly in SQL, so it was not visible to
the Auth admin delete path; it was removed manually during cleanup. Seven
content-free `evidence_deletion_audit` rows remain because that table is
append-only by design. No customer data was involved.

## Required to finish P0.2

1. Redeploy the worker and healthcheck from `main` with:
   - `DELETION_WORKER_LEASE_SECONDS=300`
   - `DELETION_MAX_ATTEMPTS=5`
   - `DELETION_WORKER_SINGLE_INSTANCE=true`
   - the existing database, Supabase, and service-role credentials.
2. Confirm the startup log contains `worker_started` with the lease, attempts,
   and single-instance values.
3. Re-run the synthetic smoke and expect `attempts=1` with a lease owner during
   processing.
4. Run the four acceptance smokes:
   - `accepted -> purged`
   - `purge_failed -> retry -> purged`
   - lease-expiry recovery
   - stale-lease / retry-overdue alert.
5. Keep one replica until the lease path is validated; then the advisory lock
   may be disabled deliberately.

The Railway credentials available in this environment are rejected by the
Railway API and CLI, so the redeploy was not executed here.
