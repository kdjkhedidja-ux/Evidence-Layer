# Deletion Phase Closure

Status date: 2026-11-05. This record freezes the deletion-phase boundary established by Waves 1–3. It is a scope closure and status report, not authorization for purge, production migration, or production restore operations.

P1 request/manifest preparation and P2 access-guard coverage are documented in `docs/deletion-full-purge-p1-request-manifest.md` and `docs/deletion-full-purge-p2-access-guard.md`; neither changes this document's full-purge non-closure.

## A. Phase intent

The phase intent was to establish:

- Scoped deletion for existing workspace-scoped evidence and relationship resources.
- Deletion proof through an append-only audit record.
- Fail-closed access blocking during account revocation/deletion states.
- Honest post-restore consistency evidence and limits.
- Bounded privilege for any future deletion worker and current user request paths.

## B. Status table

| Goal | Status | Evidence and boundary |
|---|---|---|
| Scoped deletion | PARTIAL | Existing evidence and relationship DELETE handlers require an active workspace owner and workspace/resource filters (`services/evidence-service/evidence_service/app.py:874-1023`; `supabase/migrations/20261101130000_workspace_evidence_relationships.sql:53-66,166-179`). This is not account, workspace, source, Storage, or published-snapshot purge. |
| Deletion proof + independent audit log | PASSED for evidence/relationship DELETEs | `public.evidence_deletion_audit` is forced-RLS and append-only for exposed roles; database `AFTER DELETE` triggers write actor/workspace/resource/action/result in the same transaction (`supabase/migrations/20261105100000_evidence_deletion_audit.sql`). Focused coverage verifies successful evidence/relationship events, cascade de-duplication, cross-workspace denial, and client mutation denial (`supabase/tests/evidence_deletion_audit.test.sql`). The external journal described by C0 remains deferred. |
| Block access during cancel/deletion | PARTIAL | Fresh Recovery Fence plus `account_lifecycle` checks run before protected `/v1` operations; all non-`available` states fail with 403 and missing/read errors fail closed (`services/evidence-service/evidence_service/app.py:243-289,614-1065`; `supabase/migrations/20261102120000_account_lifecycle.sql:43-145`; `tests/test_evidence_service_account_lifecycle.py:265-377`). No `workspace_lifecycle` state or workspace revocation transition exists. |
| Consistency after restore | PARTIAL | The isolated restore report records a successful local PostgreSQL restore but explicitly does not prove source equivalence, Supabase Auth/Storage behavior, or deletion-journal reconciliation (`RESTORE-DRILL-REPORT.md:4-7,71-83`). Recovery replay is local protocol evidence only; C0 deletion-journal integration and real provider PITR proof remain deferred (`docs/adr/0007-independent-recovery-ledger.md:105-125`; `docs/phase8-recovery-fence-journal-closure.md:64-82`). |
| Worker privilege bounds | PARTIAL | Current user paths use verified bearer tokens and publishable keys, and reject service-role-shaped credentials. P3 adds a dedicated non-login/non-inheriting/non-BYPASSRLS worker for source-grant revocation only (`supabase/migrations/20261105120000_account_deletion_worker_slice.sql`; `docs/deletion-full-purge-p3-worker-slice.md`). Full purge-worker bounds remain incomplete. |

## C. Delivered in Waves 2–3

- Added `public.evidence_deletion_audit` with server timestamp, verified actor, workspace, resource type/id, fixed delete/success result, and compact metadata (`supabase/migrations/20261105100000_evidence_deletion_audit.sql`).
- Added transaction-bound `AFTER DELETE` audit triggers for `evidence_records` and `evidence_relationships`; evidence FK-cascade relationship effects do not create duplicate client deletion events.
- Added forced RLS, no authenticated audit DML privileges, explicit deny policies, and append-only mutation rejection for the audit store.
- Preserved the existing owner-scoped service and BFF DELETE paths; a successful operation still returns HTTP 204 and a failed audit write rolls back the DELETE.
- Installed and exercised the account lifecycle denial gate for current `/v1` membership, workspace, source, evidence, relationship, and source-content routes. Source content rechecks before response delivery (`services/evidence-service/evidence_service/app.py:614-1065`).
- Added focused audit and lifecycle tests (`supabase/tests/evidence_deletion_audit.test.sql`; `tests/test_evidence_service_account_lifecycle.py:322-338,341-397`).
- Recorded implementation boundaries in `docs/deletion-audit-wave2.md`, `docs/deletion-guard-wave3.md`, and this closure document.

## D. Explicit non-goals and deferred work

The following are deliberately not phase deliverables:

- Account, workspace, source, Storage, or published-snapshot purge APIs and physical deletion.
- A full deletion worker, `service_role`, `BYPASSRLS`, table-owner data-plane authority, or bucket-wide Storage deletion. P3's narrowly bounded source-grant worker is documented separately.
- Workspace-specific lifecycle transitions and a `workspace_lifecycle` state model.
- External deletion-journal durability, complete target enumeration beyond the P1 account-root manifest, accepted/completed external acknowledgement, and independent journal retention.
- Production Cloudflare PITR/restore proof tied to deletion-journal replay and live Storage/data verification.
- JEV, connectors, billing, exports, and unrelated product changes.
- Local migration-guard review is now complete for `20261102120000_account_lifecycle.sql`: manifest hash, statement ledger, and catalog expectations are covered. Human production approval, a reviewed deployment plan, and remote apply verification remain separate controls (`docs/deletion-guard-wave3.md:23`; `docs/phase8-recovery-fence-journal-closure.md:51-56`).

## E. Security properties now guaranteed

- A successful evidence or relationship DELETE is owner-scoped by the service and RLS, applies workspace/resource filters, commits its database effects atomically, and produces a corresponding content-free `success` audit event. Evidence relationship FK cascades are database cleanup; the audit event is not a claim that a full purge occurred.
- The audit row contains event identity, server time, verified `auth.uid()` actor, workspace, resource type/id, fixed action/result, and only compact metadata such as relationship type. It does not contain evidence text, relationship rationale, passage bodies, tokens, signed URLs, or secrets. It is separate from the snapshot-verifier allowlist audit.
- When `account_lifecycle.state` is anything other than `available`, or the lifecycle row is absent/unreadable, protected service routes fail closed before data access. Recovery Fence failure also denies protected access. `/healthz` is the unauthenticated operational exception.
- Residual attacker-relevant risk remains: the audit is database-local rather than an independently durable external journal; direct Data/Storage policy coverage is not a complete lifecycle fence for every future surface; already delivered bytes/client copies cannot be recalled; no workspace-specific revocation gate exists; and restore/PITR can only be made deletion-aware by deferred journal reconciliation.

## F. Exit criteria statement

**CLOSED for the narrow existing evidence/relationship deletion scope under the account-scoped service gate, with P1 request/manifest preparation recorded:** the owner-scoped DELETE paths, transaction-bound database audit proof, fail-closed account lifecycle gate, account-root request/manifest model, tests, and limitations are documented coherently. **NOT CLOSED for full account/workspace deletion or production deletion-phase readiness:** purge, workspace lifecycle transitions, an independent external journal, a deletion worker, human production migration approval, and deletion-aware production restore/PITR proof remain deferred. This closure does not authorize any production migration, purge, worker, or restore operation.
