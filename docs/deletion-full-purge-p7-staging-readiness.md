# Full Purge P7: Staging Readiness

Status date: 2026-11-05. P7 is an operational preparation checklist for a separately authorized staging environment. It does not apply migrations, enable the worker, delete data, provision cloud resources, or approve production use.

## Local evidence already available

- Migration inventory and static review pass locally: 411 statements, 338 registry-approved, 0 hard-denied (`scripts/supabase_migration_guard.py`; `tests/test_supabase_migration_guard.py`).
- Local worker/control unit tests pass; pgTAP worker, ownership, and restore-reconciliation tests are authored but require a disposable local Supabase/Postgres harness.
- Worker execution is isolated behind `EVIDENCE_PURGE_DATABASE_URL` and the `account_deletion_worker` role; no browser/MCP path receives that credential (`services/evidence-service/evidence_service/purge_worker.py`; `supabase/migrations/20261105120000_account_deletion_worker_slice.sql`).
- Current purge remains non-terminal: request/account state ends at `purge_pending`, or `purge_failed` on effect/reconciliation failure. Storage, snapshots, owner memberships, workspaces, and Auth users remain.

## Staging gates

Every gate below is required before running a real worker slice in staging. A local test result is not a substitute for the corresponding staging evidence.

| Gate | Status | Required evidence |
|---|---|---|
| Isolated staging project and database identity | PENDING (human/ops) | Target identity, network boundary, no production credentials, and disposable restore plan recorded. |
| Migration preflight and apply plan | PENDING (human/ops) | Exact reviewed commit, `supabase_migration_guard.py validate/classify`, dry-run plan, rollback decision, and migration history capture. |
| Backup/PITR checkpoint | PENDING (human/ops) | Recent staging backup/PITR point, restore owner, retention horizon, and tested recovery destination. |
| Worker secret handling | PENDING (human/ops) | `EVIDENCE_PURGE_DATABASE_URL` stored in a staging secret manager, rotated, not present in browser/MCP/config files, and restricted to the dedicated worker identity. |
| Worker role and database grants | PENDING (human/ops) | Verify `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`, no inherited roles, exact table/function grants, and no Storage/snapshot privileges. |
| Synthetic acceptance and purge run | PENDING (human/ops) | Use synthetic principal/workspaces only; record claim, work-set, ordered effects, verification, audit IDs, and final `purge_pending` state. |
| Failure/retry/runbook behavior | PENDING (human/ops) | Exercise effect failure, reconciliation failure, duplicate claim, timeout, operator pause, and `purge_failed` handling. No automatic retry may widen the immutable work set. |
| Recovery/reconciliation drill | PENDING (human/ops) | Restore an isolated staging copy, keep Recovery Fence closed, run local reconciliation, and verify a resurrected target cannot become available. This is not production PITR evidence. |
| Monitoring and rollback | PENDING (human/ops) | Alerts for claim age, failed slices, restored targets, audit gaps, worker connection failures, and Recovery Fence denial; rollback/runbook owner recorded. |
| Human security/data-owner approval | PENDING (human) | Explicit approval of scope, shared-workspace retention, Storage deferral, non-terminal semantics, and no-admin-bypass operation. |

## Safe staging sequence

1. Create or select an isolated staging target and verify credentials are not production credentials.
2. Capture the backup/PITR checkpoint and record the restore owner before applying anything.
3. Run the migration guard against the exact reviewed commit and perform the documented preflight.
4. Apply migrations only after the human approval gates are recorded.
5. Verify role attributes, grants, RLS, audit append-only behavior, and account gate behavior.
6. Run one synthetic accepted account request through the worker; verify the ordered work set and all safe audit events.
7. Run duplicate claim, failure, and local restore-reconciliation scenarios; leave every request non-terminal.
8. Review monitoring output and rollback readiness; do not enable production traffic or production worker credentials.

## Explicit stop conditions

Stop and leave the account fenced if any of these occur:

- Worker credential or target identity is ambiguous.
- Migration history differs from the reviewed manifest.
- Role attributes or grants exceed the dedicated slice.
- A target is outside the immutable work set or a shared-workspace row is selected.
- Storage/snapshot deletion is requested without a separately approved exact-key policy.
- Audit, verification, or Recovery Fence status is unavailable or ambiguous.
- A restore resurrects a target and reconciliation cannot mark `purge_failed`.

## Boundary

P7 does not make the program production-ready. The final closure remains `NOT_READY_FOR_PRODUCTION` until the pending gates in `docs/DELETION-FULL-PURGE-CLOSURE.md` are independently reviewed and evidenced. P7 does not add JEV, connectors, billing, external journal implementation, Storage deletion, or terminal `purged` behavior.
