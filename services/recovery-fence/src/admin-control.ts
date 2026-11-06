import type {
  Env,
  LedgerEventPage,
  LedgerHead,
  LedgerReceipt,
  RecoverySnapshot,
  TransitionCommand,
} from "./types";
import { TransitionRejected } from "./types";
import {
  ledgerEventHash,
  parseAdminAuthorization,
  sha256Hex,
  transitionCanonical,
  verifyAdminAuthorization,
  verifyLedgerHead,
  verifyLedgerReceipt,
} from "./crypto";
import { RecoveryState } from "./state";

const ADMIN_PATH = "/internal/admin/transitions";
const MAX_REPLAY_EVENTS = 10_000;
const REPLAY_PAGE_SIZE = 250;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function singleton(env: Env): DurableObjectStub<RecoveryState> {
  const id = env.RECOVERY_STATE.idFromName("evidence-layer-recovery-v1");
  return env.RECOVERY_STATE.get(id);
}

function parseCommand(value: unknown): TransitionCommand | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const allowed = new Set([
    "event_id", "actor", "reason", "from_state", "to_state", "expected_generation",
    "expected_sequence", "expected_state_version", "sequence", "generation", "state_version",
    "reconciliation_id", "issued_at",
  ]);
  if (Object.keys(row).some((key) => !allowed.has(key)) || Object.keys(row).length !== allowed.size) return null;
  if (
    typeof row.event_id !== "string" || typeof row.actor !== "string" || typeof row.reason !== "string" ||
    typeof row.from_state !== "string" || typeof row.to_state !== "string" ||
    !Number.isSafeInteger(row.expected_generation) || !Number.isSafeInteger(row.expected_sequence) ||
    !Number.isSafeInteger(row.expected_state_version) || !Number.isSafeInteger(row.sequence) ||
    !Number.isSafeInteger(row.generation) || !Number.isSafeInteger(row.state_version) ||
    !(row.reconciliation_id === null || typeof row.reconciliation_id === "string") ||
    !Number.isSafeInteger(row.issued_at)
  ) return null;
  return row as unknown as TransitionCommand;
}

function commandMatchesReceipt(
  command: TransitionCommand,
  receipt: LedgerReceipt,
  payloadHash: string,
  previousEventHash: string,
): Promise<boolean> {
  return ledgerEventHash(previousEventHash, payloadHash).then((expectedEventHash) => (
    receipt.event_id === command.event_id && receipt.payload_hash === payloadHash &&
    receipt.actor === command.actor && receipt.reason === command.reason &&
    receipt.from_state === command.from_state && receipt.to_state === command.to_state &&
    receipt.expected_generation === command.expected_generation &&
    receipt.expected_sequence === command.expected_sequence &&
    receipt.expected_state_version === command.expected_state_version &&
    receipt.sequence === command.sequence && receipt.generation === command.generation &&
    receipt.state_version === command.state_version &&
    receipt.reconciliation_id === command.reconciliation_id &&
    receipt.issued_at === command.issued_at && receipt.durable === true &&
    receipt.previous_event_hash === previousEventHash && receipt.event_hash === expectedEventHash &&
    typeof receipt.signing_key_id === "string" && receipt.signing_key_id.length > 0 &&
    typeof receipt.ledger_event_id === "string" && receipt.ledger_event_id.length > 0
  ));
}

function sameHead(snapshot: RecoverySnapshot, head: LedgerHead): boolean {
  return snapshot.state === head.state &&
    snapshot.generation === head.generation &&
    snapshot.sequence === head.sequence &&
    snapshot.state_version === head.state_version &&
    snapshot.last_event_id === head.event_id &&
    snapshot.last_event_hash === head.event_hash;
}

async function ledgerFetch(env: Env, path: string, init?: RequestInit): Promise<Response> {
  if (!env.EVENT_LEDGER) throw new Error("ledger unavailable");
  return env.EVENT_LEDGER.fetch(`https://recovery-ledger.internal${path}`, init);
}

function freshNonce(): string {
  return `${crypto.randomUUID().replaceAll("-", "")}${crypto.randomUUID().replaceAll("-", "")}`;
}

async function readHead(env: Env, nonce: string): Promise<LedgerHead> {
  const response = await ledgerFetch(env, `/v1/head?nonce=${encodeURIComponent(nonce)}`, {
    headers: { "x-recovery-request-nonce": nonce, "cache-control": "no-store" },
  });
  if (!response.ok) throw new Error("ledger unavailable");
  const head = (await response.json()) as LedgerHead;
  if (!(await verifyLedgerHead(head, nonce, env))) throw new Error("invalid ledger head");
  return head;
}

function commandFromReceipt(receipt: LedgerReceipt): TransitionCommand {
  return {
    event_id: receipt.event_id,
    actor: receipt.actor,
    reason: receipt.reason,
    from_state: receipt.from_state,
    to_state: receipt.to_state,
    expected_generation: receipt.expected_generation,
    expected_sequence: receipt.expected_sequence,
    expected_state_version: receipt.expected_state_version,
    sequence: receipt.sequence,
    generation: receipt.generation,
    state_version: receipt.state_version,
    reconciliation_id: receipt.reconciliation_id,
    issued_at: receipt.issued_at,
  };
}

async function replayToNonOpenHead(
  env: Env,
  state: DurableObjectStub<RecoveryState>,
  start: RecoverySnapshot,
  head: LedgerHead,
): Promise<{ snapshot: RecoverySnapshot; replayed: number }> {
  if (head.state === "open" || start.sequence > head.sequence) throw new Error("unsafe reconciliation boundary");
  const eventCount = head.sequence - start.sequence;
  if (!Number.isSafeInteger(eventCount) || eventCount > MAX_REPLAY_EVENTS) {
    throw new Error("reconciliation history outside bounded replay window");
  }

  let current = start;
  let replayed = 0;
  while (current.sequence < head.sequence) {
    const after = current.sequence;
    const response = await ledgerFetch(
      env,
      `/v1/events?after_sequence=${after}&through_sequence=${head.sequence}&limit=${REPLAY_PAGE_SIZE}`,
      { headers: { "cache-control": "no-store" } },
    );
    if (!response.ok) throw new Error("ledger events unavailable");
    const page = (await response.json()) as LedgerEventPage;
    const expectedPageCount = Math.min(REPLAY_PAGE_SIZE, head.sequence - after);
    if (
      page.after_sequence !== after || page.through_sequence !== head.sequence ||
      !Array.isArray(page.events) || page.events.length !== expectedPageCount
    ) throw new Error("incomplete ledger event page");

    for (const receipt of page.events) {
      const command = commandFromReceipt(receipt);
      const payloadHash = await sha256Hex(transitionCanonical(command));
      if (
        command.expected_sequence !== current.sequence || command.sequence !== current.sequence + 1 ||
        command.expected_generation !== current.generation ||
        command.expected_state_version !== current.state_version ||
        command.from_state !== current.state || receipt.previous_event_hash !== current.last_event_hash ||
        receipt.payload_hash !== payloadHash ||
        !(await commandMatchesReceipt(command, receipt, payloadHash, current.last_event_hash)) ||
        !(await verifyLedgerReceipt(receipt, env))
      ) throw new Error("invalid or discontinuous ledger event");

      const result = await state.applyTransition(
        command,
        payloadHash,
        receipt.previous_event_hash,
        receipt.event_hash,
      );
      if (!result.ok) throw new Error("durable object rejected ledger replay");
      current = result.snapshot;
      replayed += result.idempotent ? 0 : 1;
    }
  }
  if (!sameHead(current, head)) throw new Error("replayed state does not match authoritative head");
  return { snapshot: current, replayed };
}

/**
 * Internal administrative transition callback. It is deliberately not mounted
 * by the public Recovery Worker. A separate private operator control plane must
 * authenticate and authorize the human operator before invoking this handler.
 */
export async function handleAdminTransition(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== ADMIN_PATH) return json({ error: "not found" }, 404);

  const auth = parseAdminAuthorization(request);
  if (!auth) return json({ error: "unauthorized" }, 401);
  const rawBody = await request.text();
  if (rawBody.length > 16_384 || !(await verifyAdminAuthorization(auth, request, env, rawBody))) {
    return json({ error: "unauthorized" }, 401);
  }

  let command: TransitionCommand | null;
  try {
    command = parseCommand(JSON.parse(rawBody));
  } catch {
    command = null;
  }
  if (!command || Math.abs(Math.floor(Date.now() / 1000) - command.issued_at) > 30) {
    return json({ error: "invalid transition" }, 400);
  }
  if (command.actor !== auth.keyId) return json({ error: "unauthorized" }, 403);

  const state = singleton(env);
  try {
    const payloadHash = await sha256Hex(transitionCanonical(command));
    const ledgerHead = await readHead(env, auth.nonce);
    const localSnapshot = await state.getSnapshot();
    const synchronized = sameHead(localSnapshot, ledgerHead);
    let idempotent = false;

    // If the DO has rolled back, only an administrator transition that leaves
    // the authoritative gate closed/reconciling may advance the ledger. The
    // signed immutable events are then replayed into SQLite. An open state is
    // never restored automatically from a stale mirror.
    if (!synchronized && command.to_state === "open") {
      return json({ error: "recovery reconciliation required" }, 409);
    }
    if (!synchronized && command.to_state !== "closed" && command.to_state !== "reconciling") {
      return json({ error: "recovery unavailable" }, 503);
    }

    if (synchronized) {
      const prepared = await state.prepareTransition(
        command,
        payloadHash,
        auth.nonce,
        Math.floor(Date.now() / 1000),
      );
      if (!prepared.ok) return json({ error: "invalid transition" }, 409);
      idempotent = prepared.idempotent;
    }

    const appendResponse = await ledgerFetch(env, "/v1/events/append", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ command, payload_hash: payloadHash }),
    });
    if (!appendResponse.ok) return json({ error: "recovery unavailable" }, 503);
    const receipt = (await appendResponse.json()) as LedgerReceipt;
    const expectedPreviousEventHash = command.expected_sequence === ledgerHead.sequence
      ? ledgerHead.event_hash
      : receipt.previous_event_hash;
    if (
      !(await commandMatchesReceipt(command, receipt, payloadHash, expectedPreviousEventHash)) ||
      !(await verifyLedgerReceipt(receipt, env))
    ) return json({ error: "recovery unavailable" }, 503);

    let replayed = 0;
    if (synchronized) {
      const result = await state.applyTransition(
        command,
        payloadHash,
        receipt.previous_event_hash,
        receipt.event_hash,
      );
      if (!result.ok) return json({ error: "invalid transition" }, 409);
      idempotent = result.idempotent;
    } else {
      idempotent = command.sequence <= ledgerHead.sequence;
      const replayNonce = freshNonce();
      const read = await state.readSnapshotWithNonce(replayNonce, Math.floor(Date.now() / 1000));
      if (!read.accepted || read.snapshot.sequence !== localSnapshot.sequence ||
          read.snapshot.last_event_hash !== localSnapshot.last_event_hash) {
        return json({ error: "recovery unavailable" }, 503);
      }
      const currentHead = await readHead(env, freshNonce());
      const replayResult = await replayToNonOpenHead(env, state, read.snapshot, currentHead);
      replayed = replayResult.replayed;
    }

    const finalSnapshot = await state.getSnapshot();
    const finalHead = await readHead(env, freshNonce());
    if (!sameHead(finalSnapshot, finalHead)) return json({ error: "recovery unavailable" }, 503);
    return json({
      state: finalSnapshot.state,
      generation: finalSnapshot.generation,
      sequence: finalSnapshot.sequence,
      state_version: finalSnapshot.state_version,
      event_id: command.event_id,
      idempotent,
      replayed_events: replayed,
      durable_receipt: receipt.ledger_event_id,
    });
  } catch (error) {
    if (error instanceof TransitionRejected) {
      return json({ error: error.code === "conflict" ? "transition conflict" : "invalid transition" }, 409);
    }
    return json({ error: "recovery unavailable" }, 503);
  }
}
