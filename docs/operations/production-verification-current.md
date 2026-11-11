# Production verification — current

Generated: 2026-11-11 (UTC)
Scope: Gate 0 deletion-worker readiness and the currently deployed product boundary. This is a read-only verification record. It does not claim Phase 8 closure and does not change the production flag, worker, or database.

Related documents:

- [`DELETION-WORKER-VERIFY-2026-11-11.md`](DELETION-WORKER-VERIFY-2026-11-11.md) — detailed flag/worker runbook and smoke record
- [`../IMPLEMENTATION-AUDIT-CURRENT.md`](../IMPLEMENTATION-AUDIT-CURRENT.md) — broader implementation audit
- [`../ENVIRONMENT_AND_SECRETS.md`](../ENVIRONMENT_AND_SECRETS.md) — variable and kill-switch policy
- [`../PHASE8-SCOPE-DECISIONS-2026-11-11.md`](../PHASE8-SCOPE-DECISIONS-2026-11-11.md) — Phase B gate scope decisions
- [`worker-ops-hardening-2026-11-11.md`](worker-ops-hardening-2026-11-11.md) — alerts, single-instance lock, canonical sync, backup, secret rotation

## Verified production facts

| Area | Current state | How verified |
|---|---|---|
| Web deployment | Vercel project `evidence-layer`; production deployment `dpl_DAbgSJRRs3QbUPxA3ka3ttHQBfrq` is `READY`; `https://evidencelayer.dev/` returns HTTP 200; deletion and export flags are `true` | Vercel deployments/env API and HTTP probe |
| Deletion flag | `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED=true` in production/preview/development; code default remains off unless exactly `"true"` | Vercel project env API; `apps/web/lib/deletion-flag.ts` |
| Kill switch | Set the flag to `false` (or remove it), redeploy the web app, then check the worker before re-enabling | `ENVIRONMENT_AND_SECRETS.md` and worker runbook |
| Migrations | 40 applied in production; latest `20261111180000`; repository and remote match; pending=0 | Supabase migration API plus `supabase db push --linked --dry-run` |
| Deletion requests | 23 `purged`, 8 `approval_pending`, 0 `journal_ack_pending`, 0 `accepted`, 0 `purge_pending`, 0 `purge_failed` | Read-only SQL over `public.data_deletion_requests` |
| Storage cleanup queue | 0 pending, 1 completed | Read-only SQL over `public.data_deletion_storage_queue` |
| Evidence Service | Railway service `Evidence-Layer`; deployment `44fbb6f2-0e3a-407a-8e20-fc6b6e5b18e6` is `SUCCESS`; source `alimissoum552-a11y/Evidence-Layer`; `/healthz` returns HTTP 200; export routes return 401 without auth | Railway GraphQL API and HTTP probe |
| Worker deployment | Railway service `evidence-deletion-worker`, environment `production`; deployment `eeb6449e-7a2d-41f8-bb94-4f8b254fea87` is `SUCCESS`; Dockerfile builder `services/evidence-service/Dockerfile.worker`; start `python -m evidence_service.deletion_worker`; export-cleanup calls observed | Railway GraphQL API and read-only SQL |
| Worker sizing | Exactly 1 replica; session advisory lock prevents duplicate processing; no database lease/claim function | Railway service settings and `deletion_worker.py` |
| Worker source | Connected to `alimissoum552-a11y/Evidence-Layer`, branch `main`; Evidence Service now uses the same mirror | Railway service settings |
| Worker liveness | New-code stale-ack/repair marker observed in `pg_stat_statements`; 53 calls at the time of verification | Read-only SQL |
| Worker retry behavior | `accepted`/`purge_pending` are retried every interval; `purge_failed` is not auto-retried | `deletion_worker.py` |
| Narrowed export (C6/C4) | Deployed to production: migration 40 applied, Evidence Service 44fb live, worker eeb6 live with cleanup, Vercel `dpl_DAbgSJRRs3QbUPxA3ka3ttHQBfrq` READY, `NEXT_PUBLIC_ACCOUNT_EXPORT_ENABLED=true`; export create/download/revoke smoke passed | `docs/operations/account-export.md` |
| Historical stuck request | The prior `journal_ack_pending` request was promoted by the new worker and reached `purged` | Deletion request/audit read-only SQL |
| Smoke evidence | Basic personal request `a5c0b20c-98b4-4b17-9929-13b22af5ed72` and organization/snapshot request `b115a771-197b-4666-9f50-b83cc16b05d1` reached `purged`; Auth lookup 404; workspace/snapshot counts 0 | `DELETION-WORKER-VERIFY-2026-11-11.md` |

## Deletion-queue runbook (unhealthy state)

1. Set `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED=false` (or remove it) in Vercel and redeploy the web app. The server-side `/v1/deletion-requests` routes remain authenticated and lifecycle-gated, but the UI is hidden.
2. Check the Railway worker: latest deployment status, process heartbeat/logs, `numReplicas=1`, and that the image is the reviewed worker image.
3. Run the read-only SQL diagnostics in `DELETION-WORKER-VERIFY-2026-11-11.md` for `purge_pending` age, `purge_failed`, `journal_ack_pending`, and the Storage queue.
4. Do not manually delete customer rows or queue entries outside the normal worker path. Do not increase replicas while there is no lease.
5. Re-enable the flag only after the worker is healthy and a dedicated test-account accept-to-purged smoke passes.

## Residual risks and explicit non-claims

- **PITR / full backup:** not verified or claimed. Provider PITR is disabled and the API lists 0 provider backups; a private R2 logical `public`/`private` dump from 2026-11-09 exists with a disposable restore drill, but it excludes Auth/Storage and can be stale. See `worker-ops-hardening-2026-11-11.md`.
- **Deletion absence proof:** database/Storage/Auth paths and smoke evidence are recorded, but provider backups and already-delivered client bytes are outside the claim. Phase 8 is not closed.
- **Audit retention:** the documented 365-day retention policy is not automated.
- **Worker concurrency:** no lease/claim function; exactly one replica is mandatory.
- **Failure recovery:** `purge_failed` has no automatic retry or alerting; an operator must inspect and decide.
- **Railway sources are split:** the Evidence Service deploys from `saidzeghidi31-bit/Evidence-Layer`, while the worker deploys from the `alimissoum552-a11y/Evidence-Layer` mirror. Future changes must be mirrored or Railway must be connected consistently to one reviewed repository.
- **Secret rotation:** the worker holds privileged `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; rotation and custody are not evidenced in this record.
- **Export deployment:** the narrowed account/personal export is deployed with flag `true` after a passing production create/download/revoke smoke. Shared/group export remains unavailable.
- **Phase 8:** C4/C6 are deployed for account/personal scope, and C8 is closed for the narrowed scope with a production smoke. C3 shared-workspace approvals, full C5 absence proof, C7 provider restore reconciliation, and the original full Phase 8 scope remain open/deferred. External WORM journal remains out of scope per ADR 0009.
