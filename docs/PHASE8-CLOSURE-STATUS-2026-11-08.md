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
| **C5 — fixed-scope deletion** | **PARTIAL, improved 2026-11-11** | Worker slice + exact-target verify (P3/P5), terminal `purged`, Storage/snapshot cleanup, and Auth 404 in the dedicated smoke. Full provider absence proof is deferred per the 2026-11-11 decision. |
| **C6 — export** | **NOT STARTED** | No export job, temporary bucket, or retention cleanup. |
| **C7 — restore reconciliation** | **PARTIAL** | Local restore-reconciliation contract/tests (P5); no provider-backed full data-plane restore, no deletion-journal watermark. |
| **C8 — local + operational acceptance** | **NOT MET** | Cross-gate suite and production gates are not complete; see checklist below. |

**Full Phase 8 CLOSED: NO — OPEN**, with reduced blockers (C0 external journal is no
longer one of them). **Narrowed Phase 8: CLOSED (local acceptance, 2026-11-11).**

## 2026-11-11 Phase B scope decision

The remaining gates are decided in
[`PHASE8-SCOPE-DECISIONS-2026-11-11.md`](PHASE8-SCOPE-DECISIONS-2026-11-11.md).
This table supersedes the “Remaining MUST items” list below.

| Gate | Decision | Phase C |
|---|---|---|
| C1 — isolated synthetic restore environment | **DEFER** (not an exit gate) | None |
| C2 — lifecycle + recovery checks | PASS (local) | Re-run in C8 |
| C3 — shared-workspace owner unanimity | **DEFER** (account-scope only) | None |
| C4 — mediated download links / cache / revocation | **IMPLEMENT** (account/personal export delivery) | Yes |
| C5 — full absence proof / terminal claims | **DEFER full provider proof**; accept bounded worker evidence | No new code |
| C6 — account/workspace export | **IMPLEMENT** (account/personal only) | Yes |
| C7 — provider-backed restore reconciliation | **DEFER** (fence-only) | None |
| C8 — local + operational acceptance | **IMPLEMENT** narrowed acceptance | Yes |

**Phase C MUST only:** C6 account/personal export producer, C4 mediated export
delivery/revocation, and C8 narrowed acceptance report. C1, C3, full C5 proof,
and C7 are explicit deferrals with residual risk. Phase 8 may close only as a
narrowed Phase 8 after Phase C; the original full C1–C8 scope is not met.

### 2026-11-11 Phase C implementation status

| Gate | Status | Evidence |
|---|---|---|
| C4 — mediated export download / revocation | **IMPLEMENTED (local)** | `20261111180000_phase8_account_export.sql`; grant RPCs; `account_export_grants.test.sql`; FastAPI/BFF tests |
| C6 — account/personal export | **IMPLEMENTED (local)** | Export repository, manifest/checksums, private RLS artifact, size/row caps, cleanup worker, API/UI path |
| C8 — narrowed acceptance | **CLOSED (local)** | 21 pgTAP files / 589 tests, export/worker/guard Python tests, 196 web tests, and deletion smoke `8de519e1-69ad-48d3-a2bf-d2959958d985` reaching `purged`; production export deployment is still pending |
| C1, C3, full C5, C7 | **DEFERRED** | Explicitly deferred with residual risk in `PHASE8-SCOPE-DECISIONS-2026-11-11.md` |

C6/C4 are **not deployed to production** by this change. The narrowed Phase 8
closure is a local acceptance result, not a production availability claim.

## Minimum Phase 8 exit checklist (2026-11-08 snapshot; superseded by the 2026-11-11 decision)

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

## Phase C MUST — decided 2026-11-11

Only the following items are in Phase C:

1. **C6:** build the account/personal-workspace export producer with a versioned
   manifest, checksums, private artifact, authorization, expiry, and cleanup.
2. **C4:** add the mediated export download grant, 24-hour maximum expiry,
   `private, no-store` behavior, lifecycle/dependency recheck, and revocation.
3. **C8:** publish the narrowed Phase 8 acceptance report and rerun the deletion
   worker smoke/terminal evidence.

C1 (isolated restore environment), C3 (shared-workspace unanimity), full C5
(provider absence proof), and C7 (provider restore reconciliation) are deferred.
The external immutable deletion journal remains deferred per ADR 0009. See the
scope decision record for residual risks.

## Explicit non-claims

- No WORM, Object Lock, or independent forensic journal guarantee.
- The in-database audit is database-local and is not tamper-proof against a
  database owner/DBA/service-role compromise.
- No durable external acknowledgement of deletion events.
- The live worker performs lifecycle/Auth/Storage deletion and terminal
  `purged` for the tested account path; this is not full provider absence proof
  (C5 limited claim).
- No provider PITR/full data-plane restore equivalence.
- No production export deployment or production export availability claim.
- No shared/group workspace export.
- No Phase 8 closure authorization; the separate Gate 0 production record is
  `docs/operations/production-verification-current.md`.
