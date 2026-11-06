export type RecoveryJournalState = "closed" | "reconciling" | "open";
export type JournalAggregateKind = "deletion" | "recovery";
export type JournalActorClass = "deletion-control" | "deletion-verifier" | "recovery-operator" | "restore-coordinator";
export type DeletionJournalEventKind =
  | "deletion.prepared"
  | "deletion.accepted"
  | "deletion.completed"
  | "deletion.aborted";
export type RecoveryJournalEventKind =
  | "recovery.closed"
  | "recovery.reconciling"
  | "recovery.open";
export type JournalEventKind = DeletionJournalEventKind | RecoveryJournalEventKind;

export interface RecoveryTransitionBinding {
  from_state: RecoveryJournalState;
  to_state: RecoveryJournalState;
  generation: number;
  state_version: number;
  reconciliation_ref_hash: string | null;
}

export interface UnifiedJournalEvent {
  journal_id: "evidence-layer-v1";
  schema_version: 1;
  ledger_sequence: number;
  event_id: string;
  idempotency_key_hash: string;
  aggregate_kind: JournalAggregateKind;
  aggregate_id: string;
  aggregate_sequence: number;
  event_kind: JournalEventKind;
  actor_principal: string;
  actor_class: JournalActorClass;
  reason_code: string;
  occurred_at: string;
  scope_hash: string | null;
  payload_hash: string;
  recovery_transition: RecoveryTransitionBinding | null;
  previous_event_hash: string;
  event_hash: string;
  signing_key_id: string;
  signature: string;
}

export type JournalAppendResult =
  | { ok: true; events: UnifiedJournalEvent[]; idempotent: boolean }
  | { ok: false; code: "invalid" | "conflict" | "signature" | "sequence" | "transition" };

export interface JournalSignatureVerifier {
  (keyId: string, message: string, signature: string): Promise<boolean>;
}

export const JOURNAL_GENESIS_HASH = "0".repeat(64);
export const RECOVERY_AGGREGATE_ID = "evidence-layer-recovery-v1";
const HASH_RE = /^[a-f0-9]{64}$/u;
const EVENT_ID_RE = /^[A-Za-z0-9._:-]{1,160}$/u;
const PRINCIPAL_RE = /^[A-Za-z0-9@._:/+-]{1,200}$/u;
const KEY_ID_RE = /^[A-Za-z0-9._:-]{1,100}$/u;
const DOMAIN = "evidence-unified-journal-v1";
const SIGNATURE_DOMAIN = "evidence-unified-journal-signature-v1";
const encoder = new TextEncoder();

const EVENT_KEYS = [
  "journal_id", "schema_version", "ledger_sequence", "event_id", "idempotency_key_hash",
  "aggregate_kind", "aggregate_id", "aggregate_sequence", "event_kind", "actor_principal",
  "actor_class", "reason_code", "occurred_at", "scope_hash", "payload_hash",
  "recovery_transition", "previous_event_hash", "event_hash", "signing_key_id", "signature",
].sort();

function isInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0;
}

function isRecoveryState(value: unknown): value is RecoveryJournalState {
  return value === "closed" || value === "reconciling" || value === "open";
}

function isDeletionKind(value: unknown): value is DeletionJournalEventKind {
  return value === "deletion.prepared" || value === "deletion.accepted" ||
    value === "deletion.completed" || value === "deletion.aborted";
}

function isRecoveryKind(value: unknown): value is RecoveryJournalEventKind {
  return value === "recovery.closed" || value === "recovery.reconciling" || value === "recovery.open";
}

function canonicalFields(event: UnifiedJournalEvent): string {
  const transition = event.recovery_transition;
  return JSON.stringify([
    event.journal_id,
    event.schema_version,
    event.ledger_sequence,
    event.event_id,
    event.idempotency_key_hash,
    event.aggregate_kind,
    event.aggregate_id,
    event.aggregate_sequence,
    event.event_kind,
    event.actor_principal,
    event.actor_class,
    event.reason_code,
    event.occurred_at,
    event.scope_hash,
    event.payload_hash,
    transition === null ? null : [
      transition.from_state,
      transition.to_state,
      transition.generation,
      transition.state_version,
      transition.reconciliation_ref_hash,
    ],
    event.previous_event_hash,
    event.signing_key_id,
  ]);
}

function validShape(value: unknown): value is UnifiedJournalEvent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const event = value as Record<string, unknown>;
  if (Object.keys(event).sort().join("\n") !== EVENT_KEYS.join("\n")) return false;
  if (
    event.journal_id !== "evidence-layer-v1" || event.schema_version !== 1 ||
    !isInteger(event.ledger_sequence) || !EVENT_ID_RE.test(String(event.event_id)) ||
    !HASH_RE.test(String(event.idempotency_key_hash)) ||
    (event.aggregate_kind !== "deletion" && event.aggregate_kind !== "recovery") ||
    typeof event.aggregate_id !== "string" || event.aggregate_id.length < 1 || event.aggregate_id.length > 200 ||
    !isInteger(event.aggregate_sequence) || typeof event.actor_principal !== "string" ||
    !PRINCIPAL_RE.test(event.actor_principal) ||
    !(["deletion-control", "deletion-verifier", "recovery-operator", "restore-coordinator"] as unknown[]).includes(event.actor_class) ||
    typeof event.reason_code !== "string" || !/^[a-z0-9][a-z0-9._-]{0,99}$/u.test(event.reason_code) ||
    typeof event.occurred_at !== "string" || !Number.isFinite(Date.parse(event.occurred_at)) ||
    !HASH_RE.test(String(event.payload_hash)) || !HASH_RE.test(String(event.previous_event_hash)) ||
    !HASH_RE.test(String(event.event_hash)) || !KEY_ID_RE.test(String(event.signing_key_id)) ||
    typeof event.signature !== "string" || event.signature.length < 16 || event.signature.length > 512
  ) return false;

  if (event.aggregate_kind === "deletion") {
    return isDeletionKind(event.event_kind) && typeof event.scope_hash === "string" &&
      HASH_RE.test(event.scope_hash) && event.recovery_transition === null &&
      ((event.event_kind === "deletion.completed" && event.actor_class === "deletion-verifier") ||
        (event.event_kind !== "deletion.completed" && event.actor_class === "deletion-control"));
  }

  if (
    !isRecoveryKind(event.event_kind) || event.scope_hash !== null ||
    event.aggregate_id !== RECOVERY_AGGREGATE_ID ||
    (event.actor_class !== "recovery-operator" && event.actor_class !== "restore-coordinator") ||
    (event.event_kind === "recovery.open" && event.actor_class !== "restore-coordinator")
  ) return false;
  const rawTransition = event.recovery_transition;
  if (!rawTransition || typeof rawTransition !== "object" || Array.isArray(rawTransition)) return false;
  const transition = rawTransition as RecoveryTransitionBinding;
  const transitionKeys = Object.keys(transition).sort().join("\n");
  if (transitionKeys !== [
    "from_state", "generation", "reconciliation_ref_hash", "state_version", "to_state",
  ].sort().join("\n")) return false;
  if (
    !isRecoveryState(transition.from_state) || !isRecoveryState(transition.to_state) ||
    !Number.isSafeInteger(transition.generation) || transition.generation < 0 ||
    !isInteger(transition.state_version) ||
    !(transition.reconciliation_ref_hash === null || HASH_RE.test(transition.reconciliation_ref_hash))
  ) return false;
  return (event.event_kind === "recovery.closed" && transition.to_state === "closed") ||
    (event.event_kind === "recovery.reconciling" && transition.to_state === "reconciling") ||
    (event.event_kind === "recovery.open" && transition.to_state === "open");
}

export async function sha256Hex(bytes: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(bytes));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function computeJournalEventHash(event: UnifiedJournalEvent): Promise<string> {
  return sha256Hex(`${DOMAIN}\n${event.previous_event_hash}\n${canonicalFields(event)}`);
}

export async function journalSignatureMessage(event: UnifiedJournalEvent): Promise<string> {
  const hash = await computeJournalEventHash(event);
  return `${SIGNATURE_DOMAIN}\n${event.signing_key_id}\n${hash}`;
}

function aggregateEvents(
  events: readonly UnifiedJournalEvent[],
  kind: JournalAggregateKind,
  id: string,
): UnifiedJournalEvent[] {
  return events.filter((event) => event.aggregate_kind === kind && event.aggregate_id === id);
}

function validDeletionTransition(
  previous: readonly UnifiedJournalEvent[],
  event: UnifiedJournalEvent,
): boolean {
  const prior = aggregateEvents(previous, "deletion", event.aggregate_id);
  if (event.aggregate_sequence !== prior.length + 1) return false;
  if (prior.length === 0) {
    const keyOwner = previous.find((item) => item.aggregate_kind === "deletion" &&
      item.idempotency_key_hash === event.idempotency_key_hash);
    return event.event_kind === "deletion.prepared" &&
      (!keyOwner || keyOwner.aggregate_id === event.aggregate_id);
  }
  if (prior.some((item) => item.scope_hash !== event.scope_hash ||
      item.idempotency_key_hash !== event.idempotency_key_hash)) return false;
  const last = prior.at(-1)?.event_kind;
  if (last === "deletion.prepared") {
    return event.event_kind === "deletion.accepted" || event.event_kind === "deletion.aborted";
  }
  return last === "deletion.accepted" && event.event_kind === "deletion.completed";
}

function validRecoveryTransition(
  previous: readonly UnifiedJournalEvent[],
  event: UnifiedJournalEvent,
): boolean {
  const prior = aggregateEvents(previous, "recovery", event.aggregate_id);
  const transition = event.recovery_transition;
  if (!transition || event.aggregate_sequence !== prior.length + 1) return false;
  const previousTransition = prior.at(-1)?.recovery_transition;
  const currentState = previousTransition?.to_state ?? "closed";
  const currentGeneration = previousTransition?.generation ?? 0;
  const currentVersion = previousTransition?.state_version ?? 0;
  if (
    transition.from_state !== currentState || transition.state_version !== currentVersion + 1
  ) return false;
  const allowed =
    (currentState === "closed" && transition.to_state === "reconciling") ||
    (currentState === "reconciling" && transition.to_state === "closed") ||
    (currentState === "reconciling" && transition.to_state === "open") ||
    (currentState === "open" && transition.to_state === "closed");
  if (!allowed) return false;
  if (transition.to_state === "open") {
    return transition.generation === currentGeneration + 1 &&
      transition.reconciliation_ref_hash !== null;
  }
  return transition.generation === currentGeneration && transition.reconciliation_ref_hash === null;
}

export async function appendVerifiedJournalEvent(
  history: readonly UnifiedJournalEvent[],
  event: UnifiedJournalEvent,
  verifySignature: JournalSignatureVerifier,
): Promise<JournalAppendResult> {
  if (!validShape(event)) return { ok: false, code: "invalid" };
  const eventHash = await computeJournalEventHash(event);
  if (eventHash !== event.event_hash) return { ok: false, code: "conflict" };
  if (!(await verifySignature(event.signing_key_id, await journalSignatureMessage(event), event.signature))) {
    return { ok: false, code: "signature" };
  }

  const duplicate = history.find((item) => item.event_id === event.event_id);
  if (duplicate) {
    return duplicate.event_hash === event.event_hash && canonicalFields(duplicate) === canonicalFields(event)
      ? { ok: true, events: [...history], idempotent: true }
      : { ok: false, code: "conflict" };
  }

  const last = history.at(-1);
  const expectedSequence = last ? last.ledger_sequence + 1 : 1;
  const expectedHash = last?.event_hash ?? JOURNAL_GENESIS_HASH;
  if (event.ledger_sequence !== expectedSequence || event.previous_event_hash !== expectedHash) {
    return { ok: false, code: "sequence" };
  }
  if (history.some((item) => item.ledger_sequence !== history.indexOf(item) + 1)) {
    return { ok: false, code: "sequence" };
  }

  const validTransition = event.aggregate_kind === "deletion"
    ? validDeletionTransition(history, event)
    : validRecoveryTransition(history, event);
  if (!validTransition) return { ok: false, code: "transition" };
  return { ok: true, events: [...history, event], idempotent: false };
}

export async function verifyJournalHistory(
  events: readonly UnifiedJournalEvent[],
  verifySignature: JournalSignatureVerifier,
): Promise<JournalAppendResult> {
  let verified: UnifiedJournalEvent[] = [];
  for (const event of events) {
    const result = await appendVerifiedJournalEvent(verified, event, verifySignature);
    if (!result.ok || result.idempotent) return result.ok
      ? { ok: false, code: "conflict" }
      : result;
    verified = result.events;
  }
  return { ok: true, events: verified, idempotent: false };
}

export function projectRecoveryState(
  verifiedEvents: readonly UnifiedJournalEvent[],
): { state: RecoveryJournalState; generation: number; sequence: number; state_version: number } {
  const recovery = verifiedEvents.filter((event) => event.aggregate_kind === "recovery");
  const last = recovery.at(-1)?.recovery_transition;
  return last
    ? {
      state: last.to_state,
      generation: last.generation,
      sequence: recovery.at(-1)!.aggregate_sequence,
      state_version: last.state_version,
    }
    : { state: "closed", generation: 0, sequence: 0, state_version: 0 };
}
