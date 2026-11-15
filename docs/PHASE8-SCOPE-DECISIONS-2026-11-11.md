# Phase 8 Scope Decisions — 2026-11-11

Status: product-scope decision record for the remaining Phase 8 gates. **2026-11-15 update:** C1 isolated restore drill is closed; C3 unanimous shared-workspace deletion approvals are implemented (migration 52). Full Phase 8 is **not closed** because full C5 and C7 remain provider-dependent.

Context:

- ADR 0009 already removes the external immutable deletion journal/WORM from Phase 8 exit criteria.
- Gate 0 is complete: the deletion worker is live with one replica, the flag is enabled after passing smokes, and the current production record is [`operations/production-verification-current.md`](operations/production-verification-current.md).
- The 2026-11-08 matrix remains the historical status snapshot. This document decides what Phase C may build.

## Decision summary

| Gate | Decision | Phase C work | Residual risk / limit |
|---|---|---|---|
| **C1 — isolated synthetic restore environment** | **CLOSED 2026-11-15** | Reproducible restore drill implemented and verified | Provider data-plane/Auth/Storage restore remains out of scope. |
| **C3 — shared-workspace owner unanimity** | **IMPLEMENTED 2026-11-15** | Migration 52 requests/approvals plus worker execution | Direct deletion closed; approved requests execute after a 24-hour withdrawal window. |
| **C4 — mediated download links / cache / revocation** | **IMPLEMENT in Phase C** | Account/personal export download grants, `private, no-store` behavior, expiry and revocation; no direct signed Storage URL. | Shared-workspace export download is deferred with C3. Already-delivered client bytes remain outside recall. |
| **C5 — full Storage/snapshot absence proof and terminal claims** | **DEFER beyond current evidence** | None | The accepted Phase 8 claim is limited to the current worker path: DB cleanup, snapshot/Storage queue drain, Auth 404, and terminal `purged` audit for a synthetic smoke. No provider backup, version-history, or object-version absence proof. |
| **C6 — account/workspace export** | **IMPLEMENT in Phase C (account/personal scope only)** | Account and personal-workspace export producer with manifest/checksums and expiry cleanup. Shared/group workspace export is deferred with C3. | No full shared-workspace export; no export of other members' private data. |
| **C7 — provider-backed restore reconciliation** | **DEFER** | None | Fence-only recovery remains. Accepted deletion can be resurrected by an old provider backup/PITR restore; there is no provider-backed watermark/reconciliation. |
| **C8 — acceptance** | **IMPLEMENT in Phase C** | Narrowed Phase 8 acceptance report and rerun of the deletion gate smoke. | Acceptance covers only the narrowed scope in this document, not the original C1–C8 design. |

Decision rule applied: defer unless the gate is required for the narrowed Phase 8 claim or is needed to deliver a committed data-rights capability. C4/C6 are kept because account export is a product data-rights capability. C1 and C3 were implemented on 2026-11-15; full C5 and C7 remain provider-dependent.

## C1 — Isolated synthetic restore environment

**Decision: CLOSED 2026-11-15 via the reproducible isolated restore drill.**

Rationale: the existing isolation/preflight and recovery-fence evidence is sufficient for the current narrowed claim. Standing up and proving a full isolated restore environment is infrastructure work that belongs with a future restore/PITR program, not this product increment.

Residual risk: no end-to-end restore drill, no provider-level data-plane restore proof, and no evidence that a restored environment cannot reopen stale access. Do not claim restore readiness.

## C3 — Shared-workspace owner unanimity

**Decision: IMPLEMENTED 2026-11-15; unanimous organization-admin approval is required.**

Rationale: the current product deletion scopes are personal/account and organization-owner group-workspace cleanup. A revision-bound, unanimous-owner approval workflow requires new approval tables, owner-set revision locking, withdrawal/replay handling, and UI/API work. It is not required to keep the current account-scope deletion truthful.

Residual risk: deleting an organization owner cascades the organization's group workspaces without a separate unanimous-owner vote. Shared-workspace members can lose workspace content through the owner's account deletion. This must be disclosed in product/legal copy; it is not a silent consent model.

## C4 — Mediated download links, cache behavior, revocation

**Decision: IMPLEMENT in Phase C for account/personal export delivery only.**

Required scope:

- Export download is issued as an application grant bound to the requester and export, not a direct provider signed URL.
- Grants are single-use, requester-bound, and expire no later than 24 hours after the export is ready.
- Responses use `Cache-Control: private, no-store`; no shared CDN/BFF/service-worker caching of export bytes.
- Account lifecycle, export dependency, expiry, and grant redemption are rechecked before delivery.
- Expiry and dependency revocation invalidate the grant and schedule archive deletion.

Out of scope: shared-workspace export download (deferred with C3), direct Storage/Snapshot links, and recall of bytes already delivered to a client.

Residual risk: already-downloaded client copies cannot be recalled; provider-level cache behavior must be verified before release.

## C5 — Full Storage/snapshot absence proof and terminal claims

**Decision: DEFER full provider absence proof; accept the current bounded worker evidence.**

The Phase 8 claim is limited to:

- the account/workspace rows removed by the reviewed worker path;
- source-snapshot catalog removal and Storage cleanup-queue completion observed in the worker/runbook;
- Auth identity `404` and the terminal `purged` audit after a dedicated synthetic smoke;
- explicit non-claims for provider backups, object-version history, replicas, and already-delivered bytes.

Not required in Phase C: a provider-level proof that every backup/version/replica is absent. That proof is not available from the current provider configuration and would require the deferred restore program.

Residual risk: absence is evidenced for the live worker path and tested scope, not for every retained provider copy. Do not describe the result as full data erasure.

## C6 — Account/workspace export

**Decision: IMPLEMENT in Phase C for account and personal-workspace scope only.**

Required scope:

- A signed-in subject can request an export of their account profile and personal-workspace records they own or are authorized to export.
- The archive has a versioned manifest, stable inventory, byte sizes, SHA-256 checksums, and a format version.
- The archive is built in a private export location distinct from source snapshots and is not directly addressable by a client.
- Export creation is idempotent per requester/scope, reauthorizes the current lifecycle and resources, and fails closed.
- Expired/revoked exports are deleted or marked unavailable; no new grant is issued without a new authorization check.

Out of scope: full shared/group workspace export, member-private data from other accounts, and exports containing data the caller cannot read. That scope is deferred with C3.

Residual risk: users cannot export shared-workspace content in this phase. Legal/product copy must say so.

## C7 — Provider-backed restore reconciliation

**Decision: DEFER; accept fence-only recovery with explicit residual risk.**

Rationale: the current system has a recovery fence and local reconciliation contract, but no provider-backed restore/pitr proof. Building a provider data-plane reconciliation program is out of proportion to the narrowed Phase 8 claim.

Residual risk: an old backup/PITR restore can resurrect deleted content or account rows. The external WORM journal is already out of scope (ADR 0009), and no provider backup/PITR guarantee may be claimed.

## C8 — Acceptance

**Decision: IMPLEMENT in Phase C as a narrowed Phase 8 acceptance report.**

The report must cover:

- account/personal export creation, download grant expiry, no-store behavior, dependency revocation, and cleanup;
- C4/C6 authorization and cross-tenant denial tests;
- the existing deletion worker smoke and terminal evidence (C5 limited claim);
- explicit C1/C3/C5-full/C7 deferral statements and residual risks;
- production verification references, without changing production during the acceptance run.

C8 does not validate the original full C1–C8 design. It validates only the narrowed scope agreed here.

## Phase C MUST — ordered backlog

Only these items are in Phase C:

1. **C6 export producer (account/personal only)** — durable export job, manifest/checksums, private artifact, expiry cleanup, tests.
2. **C4 mediated export delivery and revocation** — requester-bound single-use grant, 24-hour maximum, `private, no-store`, lifecycle/dependency recheck, expiry/revocation deletion, tests.
3. **C8 narrowed acceptance report** — run the export/download/revocation tests and the deletion worker smoke; record deferrals and residual risks.

C5 gets no Phase C implementation beyond the already-recorded worker evidence and the limited claim above. C1, C3, and C7 get no Phase C implementation.

## Phase 8 closure outlook

**Phase 8 cannot be marked CLOSED now.**

After Phase C completes C6, C4, and C8 for the narrowed scope, Phase 8 may be closed **only as a narrowed Phase 8** with the C1/C3/C5-full/C7 deferrals and residual risks above. The original full C1–C8 scope is not met. Any later implementation of shared-workspace approvals, full absence proof, or provider restore reconciliation must reopen the relevant gate and its acceptance work.
