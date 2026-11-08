# ADR 0009 — Phase 8 Scope: External Immutable Deletion Journal Deferred

- **Status:** Accepted — product/legal scope decision (2026-11-08).
- **Amends:** the phase-exit scope of ADR 0007 and ADR 0008 only. Their technical
  contracts remain valid.
- **Does not:** authorize Production access, migrations, deployment, purge, or
  feature-flag activation; create AWS/GCS/R2 journal infrastructure; or claim
  WORM / independent forensic guarantees.

## Context

ADR 0007 and ADR 0008 define an independent recovery/deletion journal with
provider-backed durable acknowledgement and restore-watermark replay. The
deletion closure documents treated that external journal as an unresolved C0
gate. Legal and product have now decided that an external immutable deletion
journal (WORM / independent Object Lock / independent forensic store) is **not
required** as a Phase 8 exit criterion.

## Decision

1. The **external immutable deletion journal is OUT OF SCOPE** for Phase 8
   closure and is **deferred optional hardening**. It is not a Phase 8 exit
   blocker.
2. Phase 8 acceptance relies on the controls already in the repository:
   account lifecycle gating, the in-database deletion audit, scoped purge
   slices, fail-closed access, and documented restore/fence behavior.
3. The **C0 logical-contract review remains PASSED** (design §13.6). Only the
   external-journal component is deferred; the C0 contract itself is not
   reopened.
4. No new AWS, GCS, or R2 journal infrastructure is built in Phase 8. The
   provider, key-custody, retention, and administrative-independence work in
   ADR 0007/0008 remains deferred.
5. The remaining C1–C8 gates and the C4/C5/C6/C7 requirements are **not**
   removed by this ADR; their documented PARTIAL / NOT STARTED / BLOCKED
   statuses stand.

## In scope for Phase 8

- Account lifecycle gate, fail-closed on every protected route.
- Account-scoped deletion request and atomic acceptance.
- In-database deletion audit (`public.evidence_deletion_audit`) and the
  request/manifest/work-set records as implemented.
- Scoped worker slice with exact-target verification and safe failure state.
- Delete-account flag OFF until an authenticated delete smoke passes.
- Local restore/fence protocol behavior already recorded in the repository,
  with its limits documented.

## Out of scope (deferred optional hardening)

- WORM storage, independent Object Lock, and provider-backed immutable
  retention.
- Independent forensic journal guarantees and independent administration.
- Durable external acknowledgement of deletion events.
- Journal-driven restore watermarks and cross-provider replay.
- External journal key custody, independent rotation, and retention proof.

## Explicit non-claims

- The in-database audit is **database-local**. It is not WORM and is not an
  independent forensic journal.
- A database owner, DBA, or service-role compromise can alter or delete audit
  rows despite the implemented RLS, trigger, and append-only controls.
- There is no independent durable acknowledgement of deletion acceptance.
- The restore/fence evidence is local protocol / Durable Object-level; it is
  not proof of full data-plane reconciliation, Storage/snapshot erasure, or
  Auth equivalence.
- This ADR does not claim that account deletion reaches a terminal `purged`
  state or that every account-linked object is physically erased.

## Consequences

- C0 external-journal work no longer blocks Phase 8; the C0 logical contract
  stays passed.
- Phase 8 remains **OPEN** for the in-scope gates that are still incomplete
  (see `docs/PHASE8-CLOSURE-STATUS-2026-11-08.md`).
- If legal or product later require WORM or independent forensic guarantees,
  reopen the decision through a new ADR and a separately authorized provider
  proof. Do not silently treat the in-database audit as a substitute.
