import { describe, expect, it } from "vitest";
import {
  appendVerifiedJournalEvent,
  computeJournalEventHash,
  JOURNAL_GENESIS_HASH,
  journalSignatureMessage,
  projectRecoveryState,
  RECOVERY_AGGREGATE_ID,
  verifyJournalHistory,
  type JournalSignatureVerifier,
  type UnifiedJournalEvent,
} from "../src/unified-journal-contract";

const encoder = new TextEncoder();
const SCOPE_A = "a".repeat(64);
const PAYLOAD_A = "b".repeat(64);
const IDEMPOTENCY_A = "c".repeat(64);
const RECONCILIATION_A = "d".repeat(64);

function baseEvent(overrides: Partial<UnifiedJournalEvent>): UnifiedJournalEvent {
  return {
    journal_id: "evidence-layer-v1",
    schema_version: 1,
    ledger_sequence: 1,
    event_id: "event-1",
    idempotency_key_hash: IDEMPOTENCY_A,
    aggregate_kind: "deletion",
    aggregate_id: "request-1",
    aggregate_sequence: 1,
    event_kind: "deletion.prepared",
    actor_principal: "service:deletion-control",
    actor_class: "deletion-control",
    reason_code: "scope-prepared",
    occurred_at: "2026-11-03T12:00:00.000Z",
    scope_hash: SCOPE_A,
    payload_hash: PAYLOAD_A,
    recovery_transition: null,
    previous_event_hash: JOURNAL_GENESIS_HASH,
    event_hash: "0".repeat(64),
    signing_key_id: "test-key-1",
    signature: "pending-signature-value",
    ...overrides,
  };
}

async function signerFixture() {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const sign = async (event: UnifiedJournalEvent): Promise<UnifiedJournalEvent> => {
    const eventHash = await computeJournalEventHash(event);
    const withHash = { ...event, event_hash: eventHash };
    const signature = await crypto.subtle.sign(
      "Ed25519",
      pair.privateKey,
      encoder.encode(await journalSignatureMessage(withHash)),
    );
    const bytes = new Uint8Array(signature);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return { ...withHash, signature: btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "") };
  };
  const verify: JournalSignatureVerifier = async (keyId, message, signature) => {
    if (keyId !== "test-key-1") return false;
    try {
      const normalized = signature.replaceAll("-", "+").replaceAll("_", "/");
      const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
      const decoded = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
      return await crypto.subtle.verify("Ed25519", pair.publicKey, decoded, encoder.encode(message));
    } catch {
      return false;
    }
  };
  return { sign, verify };
}

async function buildValidMixedHistory(sign: (event: UnifiedJournalEvent) => Promise<UnifiedJournalEvent>) {
  const prepared = await sign(baseEvent({}));
  const recoveryReconciling = await sign(baseEvent({
    ledger_sequence: 2,
    event_id: "event-2",
    aggregate_kind: "recovery",
    aggregate_id: RECOVERY_AGGREGATE_ID,
    aggregate_sequence: 1,
    event_kind: "recovery.reconciling",
    actor_principal: "operator:alice@example.invalid",
    actor_class: "recovery-operator",
    reason_code: "restore-reconciliation-started",
    scope_hash: null,
    previous_event_hash: prepared.event_hash,
    recovery_transition: {
      from_state: "closed",
      to_state: "reconciling",
      generation: 0,
      state_version: 1,
      reconciliation_ref_hash: null,
    },
  }));
  const accepted = await sign(baseEvent({
    ledger_sequence: 3,
    event_id: "event-3",
    aggregate_sequence: 2,
    event_kind: "deletion.accepted",
    reason_code: "local-denial-committed",
    previous_event_hash: recoveryReconciling.event_hash,
  }));
  const recoveryOpen = await sign(baseEvent({
    ledger_sequence: 4,
    event_id: "event-4",
    aggregate_kind: "recovery",
    aggregate_id: RECOVERY_AGGREGATE_ID,
    aggregate_sequence: 2,
    event_kind: "recovery.open",
    actor_principal: "operator:alice@example.invalid",
    actor_class: "restore-coordinator",
    reason_code: "restore-reconciled",
    scope_hash: null,
    previous_event_hash: accepted.event_hash,
    recovery_transition: {
      from_state: "reconciling",
      to_state: "open",
      generation: 1,
      state_version: 2,
      reconciliation_ref_hash: RECONCILIATION_A,
    },
  }));
  return [prepared, recoveryReconciling, accepted, recoveryOpen];
}

describe("unified independent journal contract", () => {
  it("projects a missing or empty Recovery history as closed", () => {
    expect(projectRecoveryState([])).toEqual({
      state: "closed", generation: 0, sequence: 0, state_version: 0,
    });
  });

  it("verifies one ordered global chain containing deletion and Recovery events", async () => {
    const { sign, verify } = await signerFixture();
    const events = await buildValidMixedHistory(sign);
    const result = await verifyJournalHistory(events, verify);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.events.map((event) => event.ledger_sequence)).toEqual([1, 2, 3, 4]);
    expect(projectRecoveryState(result.events)).toEqual({
      state: "open", generation: 1, sequence: 2, state_version: 2,
    });
    expect(projectRecoveryState(result.events.slice(0, 2))).toEqual({
      state: "reconciling", generation: 0, sequence: 1, state_version: 1,
    });
  });

  it("accepts exact retries idempotently and rejects an event ID with changed payload", async () => {
    const { sign, verify } = await signerFixture();
    const first = await sign(baseEvent({}));
    const appended = await appendVerifiedJournalEvent([], first, verify);
    expect(appended.ok).toBe(true);
    if (!appended.ok) return;
    const retry = await appendVerifiedJournalEvent(appended.events, first, verify);
    expect(retry).toMatchObject({ ok: true, idempotent: true });
    const changed = await sign({ ...first, payload_hash: "e".repeat(64), event_hash: "0".repeat(64) });
    expect(await appendVerifiedJournalEvent(appended.events, changed, verify)).toMatchObject({
      ok: false, code: "conflict",
    });
  });

  it("rejects a global sequence gap, broken hash link, and bad signature", async () => {
    const { sign, verify } = await signerFixture();
    const first = await sign(baseEvent({}));
    const sequenceGap = await sign(baseEvent({ ledger_sequence: 3, event_id: "event-gap" }));
    expect(await appendVerifiedJournalEvent([first], sequenceGap, verify)).toMatchObject({
      ok: false, code: "sequence",
    });
    const brokenLink = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-link", aggregate_sequence: 2,
      event_kind: "deletion.accepted", previous_event_hash: "f".repeat(64),
    }));
    expect(await appendVerifiedJournalEvent([first], brokenLink, verify)).toMatchObject({
      ok: false, code: "sequence",
    });
    const forged = { ...first, signature: "x".repeat(64) };
    expect(await appendVerifiedJournalEvent([], forged, verify)).toMatchObject({
      ok: false, code: "signature",
    });
  });

  it("keeps the deletion scope binding fixed and enforces the request state order", async () => {
    const { sign, verify } = await signerFixture();
    const prepared = await sign(baseEvent({}));
    const wrongScope = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-2", aggregate_sequence: 2,
      event_kind: "deletion.accepted", previous_event_hash: prepared.event_hash,
      scope_hash: "f".repeat(64),
    }));
    expect(await appendVerifiedJournalEvent([prepared], wrongScope, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const completedEarly = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-3", aggregate_sequence: 2,
      event_kind: "deletion.completed", actor_class: "deletion-verifier",
      reason_code: "absence-verified", previous_event_hash: prepared.event_hash,
    }));
    expect(await appendVerifiedJournalEvent([prepared], completedEarly, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const accepted = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-2", aggregate_sequence: 2,
      event_kind: "deletion.accepted", previous_event_hash: prepared.event_hash,
    }));
    const history = [prepared, accepted];
    const abortedAfterAcceptance = await sign(baseEvent({
      ledger_sequence: 3, event_id: "event-3", aggregate_sequence: 3,
      event_kind: "deletion.aborted", previous_event_hash: accepted.event_hash,
    }));
    expect(await appendVerifiedJournalEvent(history, abortedAfterAcceptance, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const abortedBeforeAcceptance = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-aborted", aggregate_sequence: 2,
      event_kind: "deletion.aborted", reason_code: "local-commit-absence-proven",
      previous_event_hash: prepared.event_hash,
    }));
    expect(await appendVerifiedJournalEvent([prepared], abortedBeforeAcceptance, verify)).toMatchObject({
      ok: true, idempotent: false,
    });
    const changedKey = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-key-changed", aggregate_sequence: 2,
      event_kind: "deletion.accepted", previous_event_hash: prepared.event_hash,
      idempotency_key_hash: "9".repeat(64),
    }));
    expect(await appendVerifiedJournalEvent([prepared], changedKey, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const reusedKey = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-reused-key", aggregate_id: "request-2",
      scope_hash: "8".repeat(64), previous_event_hash: prepared.event_hash,
    }));
    expect(await appendVerifiedJournalEvent([prepared], reusedKey, verify)).toMatchObject({
      ok: false, code: "transition",
    });
  });

  it("rejects a direct Recovery open, stale generation, and open without reconciliation evidence", async () => {
    const { sign, verify } = await signerFixture();
    const directOpen = await sign(baseEvent({
      aggregate_kind: "recovery", aggregate_id: RECOVERY_AGGREGATE_ID, aggregate_sequence: 1,
      event_kind: "recovery.open", actor_class: "restore-coordinator", scope_hash: null,
      recovery_transition: {
        from_state: "closed", to_state: "open", generation: 1, state_version: 1,
        reconciliation_ref_hash: RECONCILIATION_A,
      },
    }));
    expect(await appendVerifiedJournalEvent([], directOpen, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const reconciling = await sign(baseEvent({
      aggregate_kind: "recovery", aggregate_id: RECOVERY_AGGREGATE_ID, aggregate_sequence: 1,
      event_kind: "recovery.reconciling", actor_class: "recovery-operator", scope_hash: null,
      recovery_transition: {
        from_state: "closed", to_state: "reconciling", generation: 0, state_version: 1,
        reconciliation_ref_hash: null,
      },
    }));
    const staleGeneration = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-2", aggregate_kind: "recovery",
      aggregate_id: RECOVERY_AGGREGATE_ID, aggregate_sequence: 2,
      event_kind: "recovery.open", actor_class: "restore-coordinator", scope_hash: null,
      previous_event_hash: reconciling.event_hash,
      recovery_transition: {
        from_state: "reconciling", to_state: "open", generation: 0, state_version: 2,
        reconciliation_ref_hash: RECONCILIATION_A,
      },
    }));
    expect(await appendVerifiedJournalEvent([reconciling], staleGeneration, verify)).toMatchObject({
      ok: false, code: "transition",
    });
    const noEvidence = await sign(baseEvent({
      ledger_sequence: 2, event_id: "event-3", aggregate_kind: "recovery",
      aggregate_id: RECOVERY_AGGREGATE_ID, aggregate_sequence: 2,
      event_kind: "recovery.open", actor_class: "restore-coordinator", scope_hash: null,
      previous_event_hash: reconciling.event_hash,
      recovery_transition: {
        from_state: "reconciling", to_state: "open", generation: 1, state_version: 2,
        reconciliation_ref_hash: null,
      },
    }));
    expect(await appendVerifiedJournalEvent([reconciling], noEvidence, verify)).toMatchObject({
      ok: false, code: "transition",
    });
  });

  it("rejects a second writer using a stale global head after a competing append", async () => {
    const { sign, verify } = await signerFixture();
    const firstCandidate = await sign(baseEvent({ event_id: "candidate-a" }));
    const secondCandidate = await sign(baseEvent({ event_id: "candidate-b" }));
    const first = await appendVerifiedJournalEvent([], firstCandidate, verify);
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    expect(await appendVerifiedJournalEvent(first.events, secondCandidate, verify)).toMatchObject({
      ok: false, code: "sequence",
    });
  });
});
