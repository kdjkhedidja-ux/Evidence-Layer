# Worker operations hardening — 2026-11-11

Status: operational plan and current controls. This document does not rotate secrets, change backup configuration, or change the Railway project source. It records what is now enforced in code and what remains an operator action.

## 1. Alerts: purge failures and stuck pending work

A read-only health check is available as:

```text
PYTHONPATH=services/evidence-service python -m evidence_service.deletion_healthcheck
```

It reads `DELETION_HEALTHCHECK_DATABASE_URL` or `DATABASE_URL` and emits one JSON object. A production run on 2026-11-11 returned `status: ok` with no alerts. Exit codes:

- `0` — healthy;
- `2` — at least one alert;
- `1` — check failed.

Alert conditions:

| Alert | Meaning |
|---|---|
| `purge_failed_terminal` | Verification failure or exhausted attempts; inspect the request and category |
| `purge_failed_retry_overdue` | A retryable failure is past its `next_attempt_at` grace window; the worker is not draining retries |
| `stale_lease` | A request has an expired lease that no worker reclaimed; the worker may be down |
| `journal_ack_pending` | Acceptance/acknowledgement did not progress |
| `purge_pending_age` | Oldest `purge_pending` exceeds `DELETION_PURGE_PENDING_WARN_MINUTES` (default 10) |
| `accepted_age` | Oldest `accepted` exceeds `DELETION_ACCEPTED_WARN_MINUTES` (default 10) |
| `storage_queue_age` | Pending Storage cleanup object exceeds `DELETION_QUEUE_WARN_MINUTES` (default 30) |

Recommended schedule: every 5 minutes from a Railway cron service using
`services/evidence-service/railway.healthcheck.json` (or the dashboard equivalent),
with `DELETION_HEALTHCHECK_DATABASE_URL` set to a read-only/limited credential.
Page when the process exits `2` or fails to run for two consecutive periods.
The worker also emits `worker_lock_unavailable` if another instance holds the
single-instance lock; alert on that event.

## 2. Lease/claim, retry, and the no-duplicate rule

Migration `20261114230000_deletion_worker_lease_retry.sql` adds the database
lease/claim path. The worker now claims one request atomically with
`FOR UPDATE SKIP LOCKED`, records a lease owner and expiry, and can reclaim a
request only after the previous lease expires. A crashed worker therefore stops
owning the request without manual intervention.

- `DELETION_WORKER_LEASE_SECONDS` (default `300`, bounds 30-3600) controls the
  lease. Keep it above the observed worst-case request duration.
- `DELETION_MAX_ATTEMPTS` (default `5`, bounds 1-20) bounds retryable failures.
- Retry backoff is 5 minutes, 15 minutes, 1 hour, then 6 hours. Verification
  failures are terminal and are never retried.
- Failure recording and lease release require the calling worker to still own
  the lease. A stale worker cannot overwrite a newer claim.
- `DELETION_WORKER_SINGLE_INSTANCE` defaults to `true` and keeps the session
  advisory lock (`evidence-layer-deletion-worker`) as defense in depth. Set it
  to `false` only after the migration is applied and the lease path is
  validated; the lease, not the lock, is the duplicate-processing guard.
- During a rolling deploy, a new worker may wait briefly for the old worker to
  release the lock. A persistent `worker_lock_unavailable` means the old
  instance is still running or the service was scaled above one.

## 3. Railway source and canonical repository sync

Current observed state:

- Evidence Service: `alimissoum552-a11y/Evidence-Layer` (mirror), deployment `44fbb6f2-0e3a-407a-8e20-fc6b6e5b18e6`.
- Deletion worker: `alimissoum552-a11y/Evidence-Layer` (mirror), deployment `69570e18-4f2f-4707-8685-5b54dc597500`.
- The earlier Evidence Service source was `saidzeghidi31-bit/Evidence-Layer`.

For this Codespace, the git remote is `alimissoum552-a11y/Evidence-Layer`; treat
it as the canonical source unless the repository owner explicitly designates
another upstream. Both Railway services now point to it. If a separate upstream
is canonical, confirm it before the next deploy and enforce one of:

1. connect both Railway services to the canonical repository/branch; or
2. keep the mirror but block deploys until the mirror commit SHA equals the
   canonical reviewed SHA.

Deploy only commits whose SHA has passed the repository checks. Record the SHA
next to the deployment ID. A deploy without a verified SHA is a release
incident, not a routine deploy.

## 4. Backup verification and restore

Current provider facts (read-only check, 2026-11-11):

- `pitr_enabled`: **false**
- API-listed provider backups: **0**
- WALG: **enabled**
- Existing artifact: private R2 logical dump from 2026-11-09, with checksum and
  a successful disposable PostgreSQL restore drill. It covers `public` +
  `private`; it excludes `auth`, `storage`, and `extensions`.

The logical dump is a valid rollback aid for repository schema/data checks, but
it is **not** PITR and not a full Auth/Storage restore proof. Refresh the
logical dump by **2026-11-18** and record its checksum/date. Decide on provider
PITR or a scheduled managed backup by **2026-11-30**. Run a fresh restore drill
after either change. Existing procedure:
`docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-09.md`.

Restore is an incident action, not a routine release step: close the Recovery
Fence, restore into a disposable target first, validate row/FK counts, run
`python -m evidence_service.restore_reconciliation` against that target, then
follow the incident commander's production restore and deletion-reconciliation
procedure. See
`docs/operations/restore-deletion-reconciliation-2026-11-15.md`. Do not claim
PITR/backup coverage until the provider shows a recovery point and the drill
covers Auth/Storage.

## 5. Worker secret custody and rotation

Custody record: the privileged worker credentials live only in the Railway
worker secret store. They are not committed, attached to the web app, or exposed
to the browser. Last custody review: **2026-11-14**. Rotation is **not**
performed by this change.

Plan date: **2026-12-11** (or immediately on suspected exposure).

Secrets in scope:

- `SUPABASE_SERVICE_ROLE_KEY` used by the worker for Auth/Storage admin calls;
- the privileged `DATABASE_URL` password used by the worker;
- `EVIDENCE_PURGE_DATABASE_URL` for the one-shot path;
- any separate health-check database credential;
- `RECOVERY_FENCE_READ_HMAC_KEY` remains a service/fence credential and is not
  changed by the worker rotation alone.

Rotation sequence:

1. Create the replacement credential/key in Supabase.
2. Update the Railway worker environment from the secret store; never commit values.
3. Redeploy the worker and verify `worker_started`, a `worker_cycle`, and
   `deletion_healthcheck` exit `0`.
4. Run one dedicated test-account `accepted → purged` smoke and confirm Auth 404.
5. Revoke the old key/password after the worker is healthy.
6. Record rotation date, operator, deployment ID, and smoke request ID.

Do not rotate during an active incident or while a deletion request is
mid-flight unless the incident commander requires it.

## 6. Predictable deployment checklist

1. Reviewed migration plan/apply/verify. P0.2 requires migration
   `20261114230000_deletion_worker_lease_retry.sql` before the worker deploy;
   the health-check SQL references the new lease/attempt columns.
2. Deploy Evidence Service; verify `/healthz` 200 and export routes 401 without auth.
3. Deploy the worker; verify one replica, `worker_started`/`worker_cycle`, and lock acquisition.
4. Run deletion + export smokes on dedicated test accounts; verify terminal states and cleanup.
5. Enable any UI flag only after the smoke passes.
6. Record commit SHA, deployment IDs, flags, migration count, and smoke IDs.
7. Roll back to the previous Railway deployment IDs and hide the UI flag if the smoke fails.

## Residual risks

- No provider PITR or provider-listed backup; the logical dump can be stale.
- No full Auth/Storage restore proof; no provider absence proof for deleted data.
- The lease/claim path is code-complete but has not been applied or validated in
  production. Until migration 47 is applied, do not deploy the new worker or
  health-check build.
- The advisory lock remains defense in depth; horizontal scaling requires
  explicitly setting `DELETION_WORKER_SINGLE_INSTANCE=false` after validation.
- Health-check alerting depends on an operator-created cron/monitor; it is not yet a paging integration.
- Canonical repository and mirror sync remain an explicit release gate.
