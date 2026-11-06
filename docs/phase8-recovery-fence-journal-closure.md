# Phase 8 — Recovery Fence and Independent Journal Design Closure

- **Status:** CLOSED — design and local protocol contract only.
- **Date:** 2026-11-03.
- **Branch / HEAD at review:** `fix/live-explorer-css-scope` / `5fc77bf9ca29d2ecac2800b591dbae5783cc732c`.
- **Operational readiness:** BLOCKED. This closure does not prove an independent provider, live operator authorization, or rollback resistance.
- **B2:** BLOCKED. **B3:** NOT STARTED.

## Scope closed

This record closes the local Recovery Fence design and the local Independent Unified Journal contract represented by ADR 0006, ADR 0007, ADR 0008, and the Recovery Fence Workers package. It closes documentation and deterministic local protocol validation. It does not close B2 or authorize a provider, cloud resource, data ingress, C0 deletion execution, or Production use.

## Relationship between ADRs

- **ADR 0006 — Phase 8 External Recovery Fence:** defines the SQLite Durable Object as a local operational mirror, the fail-closed read boundary, and the requirement for an independently administered authority. It does not select or prove a live provider.
- **ADR 0007 — Independent Recovery Ledger and Restore Reconciliation:** records the Recovery-only local event/replay prototype and the unresolved provider, administration, and real Restore proof. Its Recovery-specific envelope describes that prototype; it does not satisfy C0's deletion journal.
- **ADR 0008 — Unified Independent Deletion and Recovery Journal:** defines the local target contract for one signed, append-only global event history covering C0 deletion events and Recovery events. It adds a global ledger sequence while preserving per-request/per-Recovery aggregate sequence, Recovery generation/state version, and fixed deletion scope binding. The local validator is not wired to a durable provider and does not claim to be the deployed journal.

These records preserve the approved architecture. ADR 0008 resolves the local event-contract shape; it does not retroactively claim that the ADR 0007 Recovery-only test ledger became a provider-backed implementation.

## Closed local contract

- Recovery defaults to `closed` when initialization or verified history is absent. Restore, missing history, sequence/signature mismatch, or uncertain reconciliation cannot open it automatically. Opening requires an explicit, authorized reconciliation event.
- Deletion uses the ordered events `prepared → accepted → completed`. Durable `prepared` evidence precedes local acceptance. The local `revocation_pending` commit is the deny point; delayed or failed external acknowledgement never restores access. `accepted` is externally acknowledged before an accepted receipt or worker execution. `completed` follows exact-target absence verification.
- `aborted` is permitted only after `prepared`, before `accepted`, and with independently verifiable evidence that the local acceptance transaction did not commit. Uncertain outcomes stay fenced; an accepted request cannot be aborted.
- Events have canonical signed fields, a previous-event hash and event hash, a contiguous global ledger sequence, and a contiguous aggregate sequence. Recovery events additionally bind `generation` and `state_version`; all deletion events for a request keep the same scope hash and idempotency binding.
- Signatures establish event integrity under a key; they do not establish operator authorization. The local verifier and ephemeral test keys do not prove actor identity, key custody, durable receipt, independent administration, provider linearizability, or retention.

## Verification evidence

Executed from `services/recovery-fence`:

```text
npm test
Test Files  2 passed (2)
Tests       28 passed (28)

npm run typecheck
tsc --noEmit — passed
```

Executed from the repository root:

```text
git diff --check — passed
New-file trailing-whitespace check — passed
```

The 28 tests include the existing Recovery Fence test file and the unified-journal contract tests. They exercise local state transitions, signature and chain validation, scope and idempotency binding, fail-closed projection, stale Recovery projection, and rejection of stale sequence/invalid deletion order. They do not exercise a real GCS writer, concurrent provider transactions, Cloudflare PITR, IAM, or durable acknowledgement.

Previously reported wider checks remain separate and were not rerun for this closure:

- Phase 7 end-to-end: `tests/test_phase7_end_to_end_rag.py` reported 9 passed and 1 failed. The reported cause was the B2-modified `secure_snapshot_read.py` no longer matching its manifest fingerprint; the HEAD version reportedly matched the expected fingerprint. No fingerprint or Phase 7 file is changed here.
- Migration Guard: `tests/test_supabase_migration_guard.py` reported 41 passed, 35 failed, and 53 subtests passed. The reported common cause was the untracked B1 migration `20261102120000_account_lifecycle.sql` not being listed in `supabase/migrations.manifest.json`. No migration, manifest, or test is changed here.

These two findings require separate review and are not failures of the Recovery Fence local contract tests.

## Change ownership and preserved work

At the start of this closure, the working tree already contained nine tracked Evidence Service/B2 modifications and untracked B1, B2, ADR 0006/0007, and Recovery Fence files. `git diff --stat` therefore reported 9 tracked files changed (`361 insertions, 69 deletions`); it did not include untracked files. This closure adds only this status record. It does not reclassify earlier files as newly created here.

The earlier B1 migration/tests, B2 Evidence Service and acceptance-test changes, ADR 0006/0007, and existing Recovery Worker files remain in the working tree. No B1/B2 source, migration, manifest, Phase 7 fingerprint, C0 design, or runtime resource was edited as part of this closure.

## Deferred operational acceptance gates

These items are outside the closed design/local-contract phase and are requirements for a separately authorized operational proof phase:

1. Select and approve an independent provider after the isolated evaluation.
2. Prove journal durability and administrative/operational independence.
3. Create and verify a private administration path with IAM identity, application authorization, approval, and audit separation.
4. Perform an actual Cloudflare Durable Object Restore/PITR drill in an isolated cloud environment, with the independent journal detecting rollback and keeping reads closed.
5. Connect the actual C0 deletion journal to the independent log and prove the complete deletion lifecycle.
6. Verify concurrent writer ordering, crash recovery, and retry/idempotency behavior against the selected provider.
7. Address the known Phase 7 fingerprint and Migration Guard conflicts in separate reviewed work.

No provider, bucket, retention lock, IAM role, key, Cloudflare namespace, or other cloud resource has been created or changed. A local pass must not be represented as independent-journal readiness.

## Final phase status

1. **Recovery Fence design and local protocol contract — CLOSED.** The documentation and local validation listed above are complete.
2. **B2 — BLOCKED** until independent journal durability, private administration, real Restore/PITR evidence, and C0 journal integration pass review.
3. **B3 — NOT STARTED.** No user-data ingress is authorized by this closure.
