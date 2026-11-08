# Phase 8 Closure Status — 2026-11-08

Scope decision: [ADR 0009](adr/0009-phase8-external-journal-scope.md) removes the
external immutable deletion journal (WORM / independent Object Lock) from Phase 8
exit criteria. It is deferred optional hardening, not a Phase 8 blocker. This
document is the status matrix under that narrower scope. It does not claim Phase
8 is closed.

## C0–C8 status

| Gate | Status | Evidence / boundary |
|---|---|---|
| **C0 — logical contract** | **PASS** | Design §13.6 review passed 2026-11-02 (`docs/data-control-phase8-design.md`). Not reopened. |
| **C0 — external immutable journal** | **OUT OF SCOPE / DEFERRED** | ADR 0009. No WORM, no independent forensic journal, no provider-backed durable acknowledgement. |
| **C1 — isolated environment** | **PARTIAL** | Isolation/preflight docs exist (`docs/deletion-full-purge-p7-staging-readiness.md`, `…-preflight-2026-11-05.md`); a full synthetic-data environment with restore capability is not proven in repository evidence. |
| **C2 — lifecycle + recovery checks** | **PASS (local)** | Lifecycle gate reads fresh state on every protected route and fails closed (`tests/test_evidence_service_account_lifecycle.py`); Recovery Fence local protocol contract passes its 28 tests (`docs/phase8-recovery-fence-journal-closure.md`). |
| **C3 — approvals + atomic acceptance** | **PARTIAL** | Account-scoped request/manifest and atomic acceptance are implemented and tested (P1/P2); shared-workspace owner unanimity is not implemented. |
| **C4 — download links / caching / revocation** | **NOT STARTED** | No mediated download-link issuance, cache invalidation, or old-link revocation. |
| **C5 — fixed-scope deletion** | **PARTIAL** | Worker slice + exact-target verify (P3/P5). No terminal `purged`, no Storage/snapshot deletion, no absence proof. |
| **C6 — export** | **NOT STARTED** | No export job, temporary bucket, or retention cleanup. |
| **C7 — restore reconciliation** | **PARTIAL** | Local restore-reconciliation contract/tests (P5); no provider-backed full data-plane restore, no deletion-journal watermark. |
| **C8 — local + operational acceptance** | **NOT MET** | Cross-gate suite and production gates are not complete; see checklist below. |

**Phase 8 CLOSED: NO — OPEN**, with reduced blockers (C0 external journal is no
longer one of them).

## Minimum Phase 8 exit checklist (in-scope only)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Lifecycle gate fail-closed on protected routes | **MET (local)** | `services/evidence-service/evidence_service/app.py`; lifecycle tests. |
| 2 | Deletion request + atomic acceptance (account scope) | **MET (local)** | P1/P2 migrations and tests; accepted state blocks data routes. |
| 3 | In-database deletion audit events as implemented | **MET (local)** | `20261105100000_evidence_deletion_audit.sql`; pgTAP audit tests; append-only/forced-RLS controls. |
| 4 | Scoped worker slice + exact-target verify | **MET (local)** | P3/P5 worker and reconciliation tests; no terminal `purged`. |
| 5 | Delete flag OFF until authenticated smoke | **MET** | `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` default off; no delete smoke run. |
| 6 | Restore/fence behavior documented, with limits | **PARTIAL** | Local recovery-fence/replay protocol tests; DO-level evidence only. Full data-plane restore/reconciliation and provider PITR are not proven. |
| 7 | No Production change without separate approval | **MET** | No production migration/RLS/Storage/deploy/flag change in this scope. |

The checklist is **not fully met**. Items 1–5 and 7 are satisfied locally; item
6 remains partial, and C1/C3/C4/C5/C6/C7 have the gaps listed above.

## Remaining MUST items for Phase 8 close (no external journal)

1. **C1:** complete an isolated synthetic-data environment with a real restore
   capability and record the evidence.
2. **C3:** implement shared-workspace owner approvals, or record an explicit
   product decision that Phase 8 is account-scope only.
3. **C4:** mediate download links, cache behavior, and old-link revocation.
4. **C5:** add the terminal `purged` state and Storage/snapshot absence proof, or
   record an explicit narrower deletion-scope decision.
5. **C6:** implement export, or record an explicit product decision that export
   is out of Phase 8.
6. **C7:** prove provider-backed restore reconciliation, or record the accepted
   fence-only limit and its residual risk.
7. **C8:** run the complete cross-gate suite and publish the acceptance report.
8. **Production:** human approval, backup/PITR evidence, and flag OFF until the
   authenticated delete smoke passes.

Deferred optional hardening (not a Phase 8 MUST): external immutable deletion
journal / WORM / independent forensic store (ADR 0009).

## Explicit non-claims

- No WORM, Object Lock, or independent forensic journal guarantee.
- The in-database audit is database-local and is not tamper-proof against a
  database owner/DBA/service-role compromise.
- No durable external acknowledgement of deletion events.
- No terminal full-account erasure, Auth identity deletion, Storage/snapshot
  physical deletion, or complete terminal completion proof.
- No provider PITR/full data-plane restore equivalence.
- No Production authorization.
