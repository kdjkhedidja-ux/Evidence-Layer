# Deletion worker verification and runbook — 2026-11-11

Status: **WORKER: live (current polling loop observed); new worker version: checklist-only**
Scope: individual-account deletion UI flag, worker entrypoints, production status reconciliation, Railway deployment template, runbook, and smoke assessment. No production migration was applied, no feature flag was changed, and no account was accepted or purged by this task.

## 1. Flag verification

| Item | Finding |
|---|---|
| Code default | Disabled unless the value is exactly `"true"` (`apps/web/lib/deletion-flag.ts`). |
| Previous behavior | Enabled unless the value was exactly `"false"`; this was the source of the drift recorded in the 2026-11-11 audit. |
| Documented behavior | `docs/ENVIRONMENT_AND_SECRETS.md` now documents default-disabled and the worker/smoke precondition. The stale “hidden until migrations are applied” paragraph was replaced with the current 37-migration/worker-status statement. |
| Production value | Not inspected from this environment. The 2026-11-09 organization closure recorded the flag as ON. The current policy is **off unless `true`**, so an unset or non-`true` production value renders the panel hidden. If production explicitly has `true`, an operator must set it to `false` (or remove it) and redeploy until the smoke below passes. |

The flag controls the account deletion panel. It does not disable the server-side `/v1/deletion-requests` routes; those remain lifecycle-gated and authenticated.

## 2. Worker code inventory

| Component | Evidence | Behavior |
|---|---|---|
| Continuous worker | `services/evidence-service/evidence_service/deletion_worker.py` | Drains the Storage queue, repairs a proven stale `journal_ack_pending`, selects up to 10 `accepted`/`purge_pending` requests, processes each, sleeps `DELETION_WORKER_INTERVAL_SECONDS` (default 60). |
| One-shot worker | `services/evidence-service/evidence_service/account_deletion_worker.py` | Runs the scoped slice, prepare, Storage cleanup, lifecycle delete, Supabase Auth admin delete, terminal mark. |
| Storage cleanup | `services/evidence-service/evidence_service/storage_cleanup_worker.py` | Claims queue rows and deletes exact object keys with the Storage admin credential. |
| Container | `services/evidence-service/Dockerfile.worker` | Runs as non-root user `10001:10001`; command `python -m evidence_service.deletion_worker`. |
| Deploy template | `services/evidence-service/railway.worker.json` | Railway Dockerfile build, start command, restart-on-failure policy. |
| Env names | `services/evidence-service/.env.worker.example` | Names only; no values committed. |

The continuous worker uses `DATABASE_URL` and passes that connection into the purge and Storage slices. The one-shot CLI path uses `EVIDENCE_PURGE_DATABASE_URL`. Both paths require `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` for Storage/Auth. Never place these variables in the web app or public service environment.

## 3. Deployment evidence

Read-only production checks were run through the Supabase Management API on 2026-11-11. No PII, tokens, connection strings, or object keys are reproduced here.

| Check | Observation | Interpretation |
|---|---|---|
| Applied migrations | 37, latest `20261110233000` | Production schema matches the checkout before this task’s new 38th migration. |
| Deletion request states | 16 `purged`, 8 `approval_pending`, 1 `journal_ack_pending`, 0 `accepted`, 0 `purge_pending`, 0 `purge_failed` | The worker path has completed historical purges. It is not currently showing a backlog. |
| Terminal audit | 16 `account_prepared`, 16 `account_purged`, 16 `purge_verified` | Historical purges reached the terminal worker path. |
| Last terminal audit | 2026-11-09 | There is no recent request to prove the worker is running today. |
| Storage queue | 0 pending, 0 completed, 0 total | No orphaned queue rows were observed. |
| Stuck acknowledgement | 1 request at `journal_ack_pending`, with 1 manifest row and 1 `manifest_frozen` audit row | This is a proven historical stuck state. The new worker code promotes exactly this state to `accepted`; it does not purge an unacknowledged request. |
| Current liveness | The worker's pending-request query counter increased from 3731 to 3732 over a 70-second read-only observation window. | The currently deployed worker loop is polling production now. This does not identify its version, replica count, or deployment receipt. |
| New-code marker | Zero `pg_stat_statements` matches for the new stale-ack `update public.data_deletion_requests as request ...` statement. | The new worker version (stale-ack repair and `v3` call) is not running in production. |

Deployment conclusion: **the currently deployed worker loop is live; the new worker version and organization purge migration are not deployed.** There is no Railway token/CLI in this Codespace and no worker log stream was available, so the worker's image revision and replica count remain unknown. Do not claim the new `v3`/stale-ack repair is live until it is deployed and observed.

## 4. Risk while the UI flag is on and the worker is down

1. A user can request and accept deletion. Acceptance immediately blocks account access at the lifecycle gate.
2. Without a running worker, the request stays at `accepted`/`purge_pending`; the user cannot use the product and cannot cancel.
3. Database cleanup, Storage deletion, Auth identity deletion, and the terminal `purged` state never complete.
4. Operators may see the user as “deleted” while data and the Auth identity still exist, creating a privacy/retention incident.

Therefore the safe default is **off unless `true`**. Do not turn it on until the deployment checklist and smoke below are both complete.

## 5. Railway deployment checklist

Run this as an operator with Railway project access. Values come only from the existing secret store/operator environment.

1. Create a Railway service from the repository root, separate from the Evidence Service.
2. Set the service config file to `services/evidence-service/railway.worker.json`. Railway is deprecating config-as-code for new services; if the dashboard rejects it, apply the same settings manually (Dockerfile, start command, one replica, restart-on-failure).
3. Confirm the build uses `services/evidence-service/Dockerfile.worker` and the start command is `python -m evidence_service.deletion_worker`.
4. Set **one replica**. The current worker has no database lease; multiple replicas can race on the same request. One replica plus restart-on-failure is the smallest safe deployment.
5. Set the required variables by name from the secret store:
   - `DATABASE_URL` — privileged database connection used by the continuous worker.
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `DELETION_WORKER_INTERVAL_SECONDS` (default 60)
   - `EVIDENCE_PURGE_DATABASE_URL` only for the one-shot CLI path.
6. Keep the worker private: no public HTTP service, no healthcheck path, no ingress.
7. Place it in the same Railway project/region as the Evidence Service where the provider allows it.
8. Configure restart-on-failure and retain at least 7 days of logs.
9. Confirm the first startup line is `{"event": "worker_started", ...}` and that cycles emit `{"event": "worker_cycle", ...}`.
10. Do not enable the web flag as part of service creation. Complete the smoke first.

If the operator cannot satisfy one replica or cannot keep the privileged URL out of the web/public service, stop and use a controlled one-off worker run instead; do not run multiple replicas against the current code.

## 6. Observability and runbook

### Log heartbeat

Each cycle emits one JSON line. Examples of the stable fields:

- `{"event":"worker_started","interval_seconds":60}`
- `{"event":"worker_cycle","repaired_stale_ack":0,"storage":{...},"accounts":[...]}`
- `{"event":"worker_error","error":"...","message":"..."}`

Alert if no `worker_cycle` line appears for 5 minutes while the service is expected to run. Treat repeated `worker_error` lines as a paging condition. Do not log or paste connection strings, service keys, object keys, or user identifiers into tickets.

### Read-only SQL checks

Use these as operator diagnostics; do not run destructive ad-hoc SQL against customer data.

```sql
-- Backlog and state counts
select status, count(*)::int as requests,
       min(created_at)::date as oldest_created,
       max(created_at)::date as newest_created
  from public.data_deletion_requests
 group by status
 order by status;

-- Accepted/purge-pending requests older than the worker interval
select request_id, status, created_at, accepted_at
  from public.data_deletion_requests
 where status in ('accepted', 'purge_pending')
   and created_at < now() - interval '10 minutes'
 order by created_at;

-- Failed terminal work
select request_id, status, purge_error_category, claimed_at
  from public.data_deletion_requests
 where status = 'purge_failed'
 order by claimed_at;

-- Proven but un-promoted manifest acknowledgements
select request.request_id, request.created_at, request.accepted_at
  from public.data_deletion_requests as request
 where request.status = 'journal_ack_pending'
   and request.accepted_at is not null
   and exists (
       select 1
         from public.evidence_deletion_audit as audit
        where audit.request_id = request.request_id
          and audit.action = 'manifest_frozen'
   );

-- Storage cleanup backlog
select count(*) filter (where completed_at is null)::int as pending,
       count(*) filter (where completed_at is not null)::int as completed,
       min(created_at) as oldest_pending
  from public.data_deletion_storage_queue;

-- Terminal audit evidence
select action, count(*)::int as events, max(occurred_at) as latest
  from public.evidence_deletion_audit
 where action in ('account_prepared', 'account_purged', 'purge_failed')
 group by action
 order by action;
```

### Retry and lease expectations

- `accepted` and `purge_pending` requests are retried every worker cycle. This is at-least-once processing, not a lease.
- `purge_failed` is intentionally not selected by the continuous worker. An operator must inspect the failure category and decide whether to requeue it; the current code does not automatically retry it.
- There is no per-request database lease. Run exactly one worker replica until a claim/lease function is designed and reviewed.
- The new worker promotes a `journal_ack_pending` request only when `accepted_at` is set and a `manifest_frozen` audit row exists. This repairs the observed historical stuck state without bypassing acceptance.
- A crash after Storage deletion but before Auth deletion is safe to retry: the 404 path is treated as success and the terminal mark is single-shot.

## 7. Correctness fix: organization owner + group workspace + snapshot

Problem: account preparation selected only personal workspaces or workspaces where the requester was the sole `owner`. Organization group workspaces give the owner an `admin` membership, and `source_snapshots.workspace_id` is `ON DELETE RESTRICT`. A group workspace with a snapshot could therefore block the Auth identity delete and leave the request at `purge_pending`.

Fix in this task:

- Added `private.deletion_workspace_is_account_owned(request_id, workspace_id)`, which recognizes personal workspaces, sole-owner workspaces, and workspaces owned by an organization whose `owner_user_id` is the requester.
- Replaced the workspace selection in snapshot purge and account preparation with this predicate.
- Aligned the worker `workspaces` select/delete RLS policies with the same predicate.
- Added `supabase/tests/account_organization_purge.test.sql` with a real organization owner, group workspace, staged snapshot, evidence row, request/accept, preparation, Storage queue assertion, Auth deletion, and terminal `purged` assertion.

The new migration is `supabase/migrations/20261111120000_deletion_org_workspace_purge.sql` (38th migration). It is **not applied to production by this task**. Apply it through the reviewed migration release process before enabling deletion. The migration was applied and the pgTAP suite passed in the local Supabase stack; it still needs the normal CI/production release path.

### Local verification performed

- `python scripts/supabase_migration_guard.py validate` — 38 reviewed migrations, manifest matches.
- `python scripts/supabase_migration_guard.py classify` — 687 statements, 591 registry-approved, 0 hard-denied.
- `python -m unittest tests.test_deletion_worker` — 3/3 pass.
- `python -m unittest tests.test_account_deletion_worker tests.test_storage_cleanup_worker` — 12/12 pass.
- `python -m unittest tests.test_supabase_migration_guard` — 74/74 pass.
- `tests.test_purge_worker` was not run: it imports `pytest`, which is not installed in this Codespace.
- `npx vitest run` — 29 files, 194/194 tests pass (including the deletion flag and account deletion UI tests).
- `npx supabase test db` — 19 files, 570 pgTAP tests pass in the local Supabase stack, including the new organization purge test.

## 8. Controlled smoke

**Result: SKIP.**

Reason: this environment has no Railway deployment control, no worker liveness stream, and no dedicated test user/Auth admin credential. Creating or accepting a real production account without a proven running worker would violate the safety rules and could strand the account. No account was touched.

A valid smoke, after the operator checklist:

1. Create a dedicated test user (never a human primary account).
2. Create a minimal personal workspace and one evidence row.
3. Request deletion and accept it.
4. Verify protected routes deny access while pending.
5. Observe the worker reach `purged` within the agreed timeout (recommend 10 minutes).
6. Verify the Auth identity is gone (`404` from the admin Auth lookup) and no `purge_pending`/`purge_failed` remains.
7. Record only the request ID, timestamps, status transitions, and aggregate counts; no PII or tokens.
8. Re-enable the UI flag only after this record is committed.

If the smoke fails or times out, leave the flag off and investigate the worker logs before any retry.

## 9. Current operator decision

- **Flag policy:** off unless `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is exactly `true`.
- **Production flag action:** if the Vercel environment currently has `true`, set it to `false` and redeploy until the worker deployment and smoke are recorded. If it is unset or already `false`, no action is required.
- **Worker status:** current polling loop is live; the new worker code (stale-ack repair and `v3` preparation call) is not deployed. Deploy it through the operator checklist before the smoke.
- **New migration:** pending reviewed apply; not applied by this task.
- **Safe order:** apply the new migration first, then deploy the new worker code, then run the smoke, then set the web flag to `true`. Deploying the worker before the migration would make `v3` unavailable.
- **Stuck acknowledgement:** one historical production request has a manifest and `manifest_frozen` audit but remains `journal_ack_pending`; the deployed worker code will repair it on its next cycle after this change is deployed. Until then, it should be tracked as a stuck deletion request.

## 10. Residual risks

1. The current worker has no lease; a second replica can race. Single replica is mandatory until a claim function is reviewed.
2. `purge_failed` has no automatic retry; an operator runbook and alert are required.
3. Historical `journal_ack_pending` rows are only repaired when the new worker version runs.
4. The new organization-workspace migration and pgTAP test are not yet exercised by the database CI in this environment.
5. The production flag value and Vercel deployment were not inspected; the operator must confirm them before claiming the UI is safe. The worker's image revision and replica count are also unknown.
