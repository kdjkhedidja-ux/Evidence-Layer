import { DurableObject } from "cloudflare:workers";
import type {
  Env,
  LedgerEventPage,
  LedgerHead,
  LedgerReceipt,
  RecoveryStateName,
  TransitionCommand,
} from "./types";
import {
  headCanonical,
  ledgerEventHash,
  receiptCanonical,
  sha256Hex,
  signEd25519,
  transitionCanonical,
} from "./crypto";

const GENESIS_EVENT_HASH = "0".repeat(64);
const MAX_EVENT_ID_LENGTH = 160;
const MAX_ACTOR_LENGTH = 160;
const MAX_REASON_LENGTH = 1000;
const MAX_PAGE_SIZE = 250;
const MAX_BODY_BYTES = 16_384;

interface HeadRow extends Record<string, string | number | null> {
  state: RecoveryStateName;
  generation: number;
  sequence: number;
  state_version: number;
  event_id: string | null;
  event_hash: string;
}

interface ReceiptRow extends Record<string, string | number | null> {
  receipt_json: string;
}

interface IdempotencyRow extends Record<string, string | number | null> {
  payload_hash: string;
  receipt_json: string;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function isState(value: unknown): value is RecoveryStateName {
  return value === "closed" || value === "reconciling" || value === "open";
}

function parseCommand(value: unknown): TransitionCommand | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const allowed = new Set([
    "event_id", "actor", "reason", "from_state", "to_state", "expected_generation",
    "expected_sequence", "expected_state_version", "sequence", "generation", "state_version",
    "reconciliation_id", "issued_at",
  ]);
  if (Object.keys(row).some((key) => !allowed.has(key)) || Object.keys(row).length !== allowed.size) {
    return null;
  }
  if (
    typeof row.event_id !== "string" || typeof row.actor !== "string" ||
    typeof row.reason !== "string" || !isState(row.from_state) || !isState(row.to_state) ||
    !Number.isSafeInteger(row.expected_generation) || !Number.isSafeInteger(row.expected_sequence) ||
    !Number.isSafeInteger(row.expected_state_version) || !Number.isSafeInteger(row.sequence) ||
    !Number.isSafeInteger(row.generation) || !Number.isSafeInteger(row.state_version) ||
    !(row.reconciliation_id === null || typeof row.reconciliation_id === "string") ||
    !Number.isSafeInteger(row.issued_at)
  ) return null;
  return row as unknown as TransitionCommand;
}

function validateTransition(
  current: HeadRow,
  command: TransitionCommand,
): "ok" | "conflict" | "invalid" {
  if (
    !command.event_id || command.event_id.length > MAX_EVENT_ID_LENGTH ||
    !command.actor || command.actor.length > MAX_ACTOR_LENGTH ||
    !command.reason || command.reason.length > MAX_REASON_LENGTH ||
    !isState(command.from_state) || !isState(command.to_state) ||
    !Number.isSafeInteger(command.issued_at) ||
    command.expected_generation !== current.generation ||
    command.expected_sequence !== current.sequence ||
    command.expected_state_version !== current.state_version ||
    command.from_state !== current.state ||
    command.sequence !== current.sequence + 1 ||
    command.state_version !== current.state_version + 1
  ) return "conflict";

  const opens = command.to_state === "open";
  const allowed =
    (current.state === "closed" && command.to_state === "reconciling") ||
    (current.state === "reconciling" && (command.to_state === "closed" || opens)) ||
    (current.state === "open" && command.to_state === "closed");
  if (!allowed) return "invalid";

  if (opens) {
    if (
      command.generation !== current.generation + 1 ||
      !command.reconciliation_id ||
      command.reconciliation_id.length > MAX_EVENT_ID_LENGTH
    ) return "invalid";
  } else if (
    command.generation !== current.generation ||
    command.reconciliation_id !== null
  ) return "invalid";

  return "ok";
}

/**
 * Independent-recovery ledger projection backed by Durable Object SQLite.
 *
 * This object is the only writer of the ledger protocol. It uses a promise
 * queue plus a SQLite transaction so a sequence number is allocated once and
 * the event, idempotency record, and head advance commit together. The
 * Durable Object is still a Cloudflare control-plane resource; it is not an
 * independently administered immutable store.
 */
export class LedgerState extends DurableObject<Env> {
  private appendQueue: Promise<void> = Promise.resolve();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS ledger_head (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          state TEXT NOT NULL CHECK (state IN ('closed', 'reconciling', 'open')),
          generation INTEGER NOT NULL CHECK (generation >= 0),
          sequence INTEGER NOT NULL CHECK (sequence >= 0),
          state_version INTEGER NOT NULL CHECK (state_version >= 0),
          event_id TEXT,
          event_hash TEXT NOT NULL
        );
        INSERT OR IGNORE INTO ledger_head
          (singleton, state, generation, sequence, state_version, event_id, event_hash)
          VALUES (1, 'closed', 0, 0, 0, NULL, '${GENESIS_EVENT_HASH}');
        CREATE TABLE IF NOT EXISTS ledger_events (
          sequence INTEGER PRIMARY KEY,
          event_id TEXT NOT NULL UNIQUE,
          payload_hash TEXT NOT NULL,
          previous_event_hash TEXT NOT NULL,
          event_hash TEXT NOT NULL,
          receipt_json TEXT NOT NULL,
          inserted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger_idempotency (
          event_id TEXT PRIMARY KEY,
          payload_hash TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          receipt_json TEXT NOT NULL
        );
      `);
    });
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/v1/head") {
      return this.readHead(request, url);
    }
    if (request.method === "GET" && url.pathname === "/v1/events") {
      return this.readEvents(url);
    }
    if (request.method === "POST" && url.pathname === "/v1/events/append") {
      return this.enqueue(() => this.append(request));
    }
    return json({ error: "not found" }, 404);
  }

  private enqueue(task: () => Promise<Response>): Promise<Response> {
    const run = this.appendQueue.then(task, task);
    this.appendQueue = run.then(() => undefined, () => undefined);
    return run;
  }

  private currentHead(): HeadRow {
    return this.ctx.storage.sql
      .exec<HeadRow>("SELECT * FROM ledger_head WHERE singleton = 1")
      .toArray()[0];
  }

  private async readHead(request: Request, url: URL): Promise<Response> {
    const nonce = url.searchParams.get("nonce") ?? "";
    if (!nonce || request.headers.get("x-recovery-request-nonce") !== nonce) {
      return json({ error: "invalid nonce" }, 400);
    }
    if (!this.env.LEDGER_PRIVATE_KEY || !this.env.LEDGER_KEY_ID) {
      return json({ error: "signing unavailable" }, 503);
    }
    const current = this.currentHead();
    const head: LedgerHead = {
      state: current.state,
      generation: current.generation,
      sequence: current.sequence,
      state_version: current.state_version,
      event_id: current.event_id,
      event_hash: current.event_hash,
      checkpoint_id: `checkpoint-${current.sequence}`,
      signing_key_id: this.env.LEDGER_KEY_ID,
      issued_at: Date.now(),
      request_nonce: nonce,
      signature: "",
    };
    try {
      head.signature = await signEd25519(this.env.LEDGER_PRIVATE_KEY, headCanonical(head));
    } catch {
      return json({ error: "signing unavailable" }, 503);
    }
    return json(head);
  }

  private readEvents(url: URL): Response {
    const after = Number(url.searchParams.get("after_sequence"));
    const through = Number(url.searchParams.get("through_sequence"));
    const limit = Number(url.searchParams.get("limit"));
    if (
      !Number.isSafeInteger(after) || after < 0 ||
      !Number.isSafeInteger(through) || through < after ||
      !Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE
    ) return json({ error: "invalid page" }, 400);

    const rows = this.ctx.storage.sql
      .exec<ReceiptRow>(
        "SELECT receipt_json FROM ledger_events WHERE sequence > ? AND sequence <= ? ORDER BY sequence LIMIT ?",
        after,
        through,
        limit,
      )
      .toArray();
    if (through > after && rows.length > 0) {
      const first = JSON.parse(rows[0].receipt_json) as LedgerReceipt;
      if (first.sequence !== after + 1) return json({ error: "ledger gap" }, 503);
    }
    const page: LedgerEventPage = {
      after_sequence: after,
      through_sequence: through,
      events: rows.map((row) => JSON.parse(row.receipt_json) as LedgerReceipt),
    };
    return json(page);
  }

  private async append(request: Request): Promise<Response> {
    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) return json({ error: "invalid append" }, 400);
    let body: { command?: unknown; payload_hash?: unknown };
    try {
      body = JSON.parse(raw) as { command?: unknown; payload_hash?: unknown };
    } catch {
      return json({ error: "invalid append" }, 400);
    }
    const command = parseCommand(body.command);
    const providedPayloadHash = body.payload_hash;
    if (
      !command || typeof providedPayloadHash !== "string" ||
      !/^[a-f0-9]{64}$/u.test(providedPayloadHash)
    ) return json({ error: "invalid append" }, 400);

    const payloadHash = await sha256Hex(transitionCanonical(command));
    if (payloadHash !== providedPayloadHash) return json({ error: "payload mismatch" }, 409);

    const existing = this.ctx.storage.sql
      .exec<IdempotencyRow>(
        "SELECT payload_hash, receipt_json FROM ledger_idempotency WHERE event_id = ?",
        command.event_id,
      )
      .toArray()[0];
    if (existing) {
      if (existing.payload_hash !== payloadHash) return json({ error: "idempotency conflict" }, 409);
      return new Response(existing.receipt_json, {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
      });
    }

    const current = this.currentHead();
    const decision = validateTransition(current, command);
    if (decision !== "ok") return json({ error: "invalid transition" }, 409);

    if (!this.env.LEDGER_PRIVATE_KEY || !this.env.LEDGER_KEY_ID) {
      return json({ error: "signing unavailable" }, 503);
    }

    const eventHash = await ledgerEventHash(current.event_hash, payloadHash);
    const receipt: LedgerReceipt = {
      ...command,
      durable: true,
      payload_hash: payloadHash,
      previous_event_hash: current.event_hash,
      event_hash: eventHash,
      ledger_event_id: `ledger-${command.sequence}`,
      signing_key_id: this.env.LEDGER_KEY_ID,
      signature: "",
    };
    try {
      receipt.signature = await signEd25519(this.env.LEDGER_PRIVATE_KEY, receiptCanonical(receipt));
    } catch {
      return json({ error: "signing unavailable" }, 503);
    }

    try {
      this.ctx.storage.transactionSync(() => {
        const insertedAt = new Date().toISOString();
        this.ctx.storage.sql.exec(
          `INSERT INTO ledger_events
             (sequence, event_id, payload_hash, previous_event_hash, event_hash, receipt_json, inserted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)`,
          command.sequence,
          command.event_id,
          payloadHash,
          current.event_hash,
          eventHash,
          JSON.stringify(receipt),
          insertedAt,
        );
        this.ctx.storage.sql.exec(
          `INSERT INTO ledger_idempotency (event_id, payload_hash, sequence, receipt_json)
           VALUES (?, ?, ?, ?)`,
          command.event_id,
          payloadHash,
          command.sequence,
          JSON.stringify(receipt),
        );
        this.ctx.storage.sql.exec(
          `UPDATE ledger_head
           SET state = ?, generation = ?, sequence = ?, state_version = ?, event_id = ?, event_hash = ?
           WHERE singleton = 1`,
          command.to_state,
          command.generation,
          command.sequence,
          command.state_version,
          command.event_id,
          eventHash,
        );
      });
    } catch {
      return json({ error: "append not durable" }, 503);
    }

    return json(receipt);
  }
}
