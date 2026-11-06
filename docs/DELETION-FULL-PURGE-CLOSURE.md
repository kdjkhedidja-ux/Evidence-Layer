# Full-Purge Deletion Closure

Status date: 2026-11-05. This is the final P0–P5 documentation closure for the locally implemented account-scoped purge program. It is not production approval, a production migration authorization, or proof of provider PITR behavior.

## A. Final goal status

| Goal | Final label | Evidence and strict boundary |
|---|---|---|
| Scoped deletion | PARTIAL | The worker freezes and purges source grants, principal `member` memberships, relationships, and evidence only in sole-active-owner/no-active-member workspaces (`supabase/migrations/20261105130000_account_application_purge_slice.sql`; `supabase/migrations/20261105140000_account_evidence_ownership_purge.sql`). Owner memberships, workspace rows, shared data, snapshots, Storage, and Auth identities remain. |
| Deletion proof + independent audit log | PARTIAL | `public.evidence_deletion_audit` is forced-RLS, append-only for exposed roles, and records request, worker, delete, work-set, verification, and reconciliation events (`supabase/migrations/20261105100000_evidence_deletion_audit.sql`; `20261105150000_deletion_restore_reconciliation.sql`). It is database-local; the independent C0 journal and durable external acknowledgement remain unsatisfied (`docs/adr/0007-independent-recovery-ledger.md:115-126`; `docs/phase8-recovery-fence-journal-closure.md:15-27`). |
| Block access during cancel/deletion | PARTIAL | The Recovery Fence and fresh `account_lifecycle` read deny every protected `/v1` route unless state is `available` (`services/evidence-service/evidence_service/app.py:264-300`; `tests/test_evidence_service_account_lifecycle.py:265-484`). The design is account-scoped for this request, so other members remain unaffected; there is no workspace lifecycle state or full cancel/approval model. |
| Consistency after restore | PARTIAL | Local reconciliation checks exact frozen targets and marks restored-target inconsistency `purge_failed` without reopening access (`supabase/migrations/20261105150000_deletion_restore_reconciliation.sql`; `supabase/tests/deletion_restore_reconciliation.test.sql`; `docs/deletion-full-purge-p5-restore-reconciliation.md`). The isolated restore report explicitly does not prove source equivalence, Auth/Storage behavior, or provider/PITR deletion reconciliation (`RESTORE-DRILL-REPORT.md:4-7,71-83`). |
| Worker privilege bounds | PARTIAL | `account_deletion_worker` is `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`, has request-bound policies, no unrelated evidence SELECT, no Storage/snapshot privilege, and runs through `services/evidence-service/evidence_service/purge_worker.py` (`supabase/migrations/20261105120000_account_deletion_worker_slice.sql`). Full target execution, leases/retries, production secret custody, and operational review are not complete. |

No goal is labeled `FAILED`; no goal is labeled `PASSED` for unrestricted full-purge readiness because physical object deletion, terminal completion, independent journal durability, and production restore proof are open.

## B. What the system can do today

For an externally accepted account request, the dedicated worker process can run `private.run_account_deletion_slice(request_id)` through the operator-managed `EVIDENCE_PURGE_DATABASE_URL` CLI path (`services/evidence-service/evidence_service/purge_worker.py`). The request must be in `accepted`; concurrent or replayed claims fail rather than create a second claim.

The ordered local slice is:

1. Lock and claim the request; set request and account lifecycle to `purge_pending`.
2. Freeze exact type/id work-set targets.
3. Delete principal `source_grants`.
4. Delete principal `workspace_memberships` where `role = 'member'`.
5. Delete relationships in eligible workspaces.
6. Delete evidence in workspaces where the principal is the sole active owner and no other active members exist.
7. Verify every frozen target is absent.
8. Keep request and lifecycle at `purge_pending`; on effect or verification error, set both to `purge_failed` and audit a safe failure category.

The accepted account remains blocked throughout. `private.reconcile_account_deletion_slice(request_id)` rechecks the immutable work set after a local restore or other recovery event. Clean reconciliation records `reconciliation_verified` but does not open access; a resurrected target records `reconciliation_failed` and moves to `purge_failed`.

The user-facing control APIs are `POST /v1/deletion-requests`, `POST /v1/deletion-requests/{request_id}/accept`, and `GET /v1/deletion-requests/{request_id}`. Only the requester’s content-free status remains available while blocked; data routes remain denied. `/healthz` is the operational unauthenticated exception.

## C. Explicit non-capabilities

Operators must not assume that the current program:

- Physically deleted staged or published snapshot bytes.
- Deleted any `storage.objects` row, bucket object, or bucket-wide key.
- Deleted immutable `source_snapshots` catalog rows or publication metadata.
- Deleted owner memberships, workspaces, shared-workspace evidence, or another user’s memberships.
- Deleted an Auth user or all account-linked data outside the documented work set.
- Reached lifecycle/request state `purged` or produced a complete terminal completion proof.
- Has an independent external deletion journal, accepted/completed receipt, or durable provider acknowledgement.
- Proved Cloudflare/Supabase PITR restore behavior, object-store retention, cache invalidation, old-link invalidation, or Auth/Storage equivalence.
- Recalled bytes already delivered to clients or erased copies held outside the database.

## D. Production publish checklist

These are release gates, not claims made by local tests. Human approval remains required.

| Gate | Status | Evidence/owner action |
|---|---|---|
| Required security/data-owner reviewers approve scope, ownership rules, and no-admin-bypass operation | PENDING (human) | No production approval is recorded; design requirements remain in `docs/data-control-phase8-design.md:47-50,80-103,425-432`. |
| Recent backup/PITR window and restore procedure verified for the production project | PENDING (human) | `RESTORE-DRILL-REPORT.md` is isolated local PostgreSQL evidence only and explicitly excludes Supabase Auth/Storage/PITR equivalence. |
| Migration manifest, statement ledger, guard plan, preflight, rollback, and production apply path reviewed | PENDING (human) | Local guard is green: 411 statements, 338 registry-approved, 0 hard-denied; this does not authorize remote apply. |
| Worker secret custody and execution isolation reviewed | PENDING (human) | `EVIDENCE_PURGE_DATABASE_URL` is required by the CLI, but production secret-manager/network/rotation controls are not proven here. |
| Monitoring, alerting, lease/retry behavior, `purge_failed` handling, and rollback/runbook approved | PENDING (human) | Local worker has claim/effect/verify and safe failure states; production operations are not exercised. |
| Storage/snapshot exact-key policy and absence verification approved | PENDING (human) | No physical Storage/snapshot delete is implemented; published metadata/receipt and staged-object policy remain open. |
| Independent journal and restore reconciliation authority approved | PENDING (human) | ADR 0007/0008 and the Recovery Fence closure explicitly leave C0/provider integration unsatisfied. |

No checklist item authorizes production publication by itself. The current boundary must remain disabled for production purge execution until the pending human and operational gates are closed.

## E. Post-closure backlog

1. Approve and implement the independent deletion journal/recovery-authority integration, including prepared/accepted/completed durable acknowledgement and replay ordering.
2. Define production worker leases, retries, observability, secret custody, network isolation, and operator handling for `purge_failed`.
3. Resolve owner/workspace retention and remaining account target enumeration, including any personal-workspace and evidence ownership fields needed beyond the sole-owner rule.
4. Define exact staged-object work sets and verified Storage deletion; separately approve published-object deletion, immutable publication receipts, metadata retention, and absence probes.
5. Perform provider-backed PITR/restore reconciliation covering Auth, database rows, Storage objects, caches, old links, lifecycle fences, and deletion journal watermark.
6. Add the terminal `purged` transition and completion proof only after every approved live target, retained metadata, backup fact, and external journal event is verified.

JEV, connectors, billing, and unrelated product scope remain out of this backlog.

## F. Verdict

**NOT_READY_FOR_PRODUCTION** — the local bounded purge and reconciliation slice is documented and testable, but full-purge readiness is not established while Storage/snapshot deletion, terminal `purged`, independent journal durability, human production approval, and provider PITR evidence remain open.
