import { env } from "cloudflare:test";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { fetchHandler } from "../src/index";
import { handleAdminTransition } from "../src/admin-control";
import {
  headCanonical,
  ledgerEventHash,
  receiptCanonical,
  sha256Hex,
} from "../src/crypto";
import type {
  Env,
  LedgerHead,
  LedgerReceipt,
  RecoveryStateName,
  TransitionCommand,
} from "../src/types";

const encoder = new TextEncoder();
const READ_SECRET = "local-only-recovery-reader-secret-32bytes";

function base64url(data: ArrayBuffer | Uint8Array): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

async function signEd25519(key: CryptoKey, payload: string): Promise<string> {
  return base64url(await crypto.subtle.sign("Ed25519", key, encoder.encode(payload)));
}

async function signHmac(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return base64url(await crypto.subtle.sign("HMAC", key, encoder.encode(payload)));
}

interface MutableLedgerHead {
  state: RecoveryStateName;
  generation: number;
  sequence: number;
  state_version: number;
  event_id: string | null;
  event_hash: string;
}

class ProtocolLedger {
  current: MutableLedgerHead = {
    state: "closed",
    generation: 0,
    sequence: 0,
    state_version: 0,
    event_id: null,
    event_hash: "0".repeat(64),
  };
  receipts = new Map<string, { payloadHash: string; receipt: LedgerReceipt }>();
  events: LedgerReceipt[] = [];
  verifiedReconciliations = new Set(["reconciliation:test-001", "verified-restore-watermark-1"]);
  unavailableHead = false;
  unavailableAppend = false;
  corruptNextReceipt = false;
  corruptHead = false;
  missingEventSequence: number | null = null;
  corruptEventSequence: number | null = null;
  private appendQueue: Promise<void> = Promise.resolve();

  constructor(
    private readonly privateKey: CryptoKey,
    readonly signingKeyId = "ledger-current",
  ) {}

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/v1/head") {
      if (this.unavailableHead) return new Response(null, { status: 503 });
      const nonce = url.searchParams.get("nonce") ?? "";
      if (request.headers.get("x-recovery-request-nonce") !== nonce) {
        return new Response(null, { status: 400 });
      }
      const head: LedgerHead = {
        ...this.current,
        checkpoint_id: `checkpoint-${this.current.sequence}`,
        signing_key_id: this.signingKeyId,
        issued_at: Date.now(),
        request_nonce: nonce,
        signature: "",
      };
      head.signature = await signEd25519(this.privateKey, headCanonical(head));
      if (this.corruptHead) head.signature = "invalid-signature";
      return Response.json(head);
    }
    if (request.method === "GET" && url.pathname === "/v1/events") {
      const afterSequence = Number(url.searchParams.get("after_sequence"));
      const throughSequence = Number(url.searchParams.get("through_sequence"));
      const limit = Number(url.searchParams.get("limit"));
      if (!Number.isSafeInteger(afterSequence) || !Number.isSafeInteger(throughSequence) ||
          !Number.isSafeInteger(limit) || limit < 1) return new Response(null, { status: 400 });
      const events = this.events
        .filter((event) => event.sequence > afterSequence && event.sequence <= throughSequence)
        .slice(0, limit)
        .filter((event) => event.sequence !== this.missingEventSequence)
        .map((event) => event.sequence === this.corruptEventSequence
          ? { ...event, signature: "corrupt-event-signature" }
          : event);
      return Response.json({ after_sequence: afterSequence, through_sequence: throughSequence, events });
    }
    if (request.method === "POST" && url.pathname === "/v1/events/append") {
      if (this.unavailableAppend) return new Response(null, { status: 503 });
      const body = await request.json() as { command: TransitionCommand; payload_hash: string };
      const task = this.appendQueue.then(() => this.append(body.command, body.payload_hash));
      this.appendQueue = task.then(() => undefined, () => undefined);
      return task;
    }
    return new Response(null, { status: 404 });
  };

  private async append(command: TransitionCommand, payloadHash: string): Promise<Response> {
    const existing = this.receipts.get(command.event_id);
    if (existing) {
      if (existing.payloadHash !== payloadHash) return new Response(null, { status: 409 });
      return Response.json(existing.receipt);
    }
    if (
      command.from_state !== this.current.state ||
      command.expected_generation !== this.current.generation ||
      command.expected_sequence !== this.current.sequence ||
      command.expected_state_version !== this.current.state_version ||
      command.sequence !== this.current.sequence + 1 ||
      command.state_version !== this.current.state_version + 1
    ) return new Response(null, { status: 409 });
    if (
      !(
        (this.current.state === "closed" && command.to_state === "reconciling") ||
        (this.current.state === "reconciling" && command.to_state === "closed") ||
        (this.current.state === "reconciling" && command.to_state === "open" &&
          command.reconciliation_id && this.verifiedReconciliations.has(command.reconciliation_id)) ||
        (this.current.state === "open" && command.to_state === "closed")
      )
    ) return new Response(null, { status: 409 });

    const opens = command.to_state === "open";
    if (command.generation !== this.current.generation + (opens ? 1 : 0)) {
      return new Response(null, { status: 409 });
    }
    const receipt: LedgerReceipt = {
      ...command,
      durable: true,
      payload_hash: payloadHash,
      previous_event_hash: this.current.event_hash,
      event_hash: await ledgerEventHash(this.current.event_hash, payloadHash),
      ledger_event_id: `ledger-${command.sequence}`,
      signing_key_id: this.signingKeyId,
      signature: "",
    };
    receipt.signature = await signEd25519(this.privateKey, receiptCanonical(receipt));
    this.current = {
      state: command.to_state,
      generation: command.generation,
      sequence: command.sequence,
      state_version: command.state_version,
      event_id: command.event_id,
      event_hash: receipt.event_hash,
    };
    this.receipts.set(command.event_id, { payloadHash, receipt });
    this.events.push(receipt);
    if (this.corruptNextReceipt) {
      this.corruptNextReceipt = false;
      receipt.signature = "invalid";
    }
    return Response.json(receipt);
  }

  simulateAuthoritativeHeadAhead(): void {
    this.current = {
      state: "reconciling",
      generation: 0,
      sequence: this.current.sequence + 1,
      state_version: this.current.state_version + 1,
      event_id: "ledger-only-newer-event",
      event_hash: "f".repeat(64),
    };
  }
}

let adminKeyPair: CryptoKeyPair;
let previousAdminKeyPair: CryptoKeyPair;
let ledgerKeyPair: CryptoKeyPair;
let previousLedgerKeyPair: CryptoKeyPair;
let adminPublic: string;
let previousAdminPublic: string;
let ledgerPublic: string;
let previousLedgerPublic: string;

beforeAll(async () => {
  adminKeyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  previousAdminKeyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  ledgerKeyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  previousLedgerKeyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  adminPublic = base64url(await crypto.subtle.exportKey("raw", adminKeyPair.publicKey));
  previousAdminPublic = base64url(await crypto.subtle.exportKey("raw", previousAdminKeyPair.publicKey));
  ledgerPublic = base64url(await crypto.subtle.exportKey("raw", ledgerKeyPair.publicKey));
  previousLedgerPublic = base64url(await crypto.subtle.exportKey("raw", previousLedgerKeyPair.publicKey));
});

afterAll(() => undefined);

function workerEnv(ledger: ProtocolLedger, name: string = crypto.randomUUID()): Env {
  const cloudflareEnv = env as unknown as Env;
  const namespace = cloudflareEnv.RECOVERY_STATE;
  const isolatedId = namespace.idFromName(`test-${name}`);
  const isolatedNamespace = {
    idFromName: (_objectName: string) => isolatedId,
    get: (_id: DurableObjectId) => namespace.get(isolatedId),
  } as DurableObjectNamespace<import("../src/state").RecoveryState>;
  return {
    ...cloudflareEnv,
    RECOVERY_STATE: isolatedNamespace,
    EVENT_LEDGER: ledger as unknown as Fetcher,
    READ_KEY_ID: "reader-current",
    READ_KEY_SECRET: READ_SECRET,
    READ_PREVIOUS_KEY_ID: "reader-previous",
    READ_PREVIOUS_KEY_SECRET: "local-only-old-recovery-reader-secret-32bytes",
    ADMIN_KEY_ID: "operator-current",
    ADMIN_PUBLIC_KEY: adminPublic,
    ADMIN_PREVIOUS_KEY_ID: "operator-previous",
    ADMIN_PREVIOUS_PUBLIC_KEY: previousAdminPublic,
    LEDGER_PUBLIC_KEY: ledgerPublic,
    LEDGER_KEY_ID: "ledger-current",
    LEDGER_PREVIOUS_KEY_ID: "ledger-previous",
    LEDGER_PREVIOUS_PUBLIC_KEY: previousLedgerPublic,
  };
}

function readRequest(nonce = crypto.randomUUID().replaceAll("-", "") + "0000000000000000"): Request {
  return new Request("https://recovery.test/v1/recovery", { method: "GET", headers: { "x-test-nonce": nonce } });
}

async function authorizedRead(request = readRequest(), keyId = "reader-current", secret = READ_SECRET) {
  const nonce = request.headers.get("x-test-nonce")!;
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = await signHmac(
    secret,
    `GET\nhttps://recovery.test\n/v1/recovery\n${keyId}\n${timestamp}\n${nonce}`,
  );
  const headers = new Headers(request.headers);
  headers.set("x-recovery-key-id", keyId);
  headers.set("x-recovery-timestamp", String(timestamp));
  headers.set("x-recovery-nonce", nonce);
  headers.set("x-recovery-signature", signature);
  return new Request(request.url, { method: "GET", headers });
}

async function commandFor(
  current: MutableLedgerHead,
  to: RecoveryStateName,
  options: { eventId?: string; reconciliationId?: string | null; actor?: string; reason?: string } = {},
): Promise<TransitionCommand> {
  const opens = to === "open";
  return {
    event_id: options.eventId ?? crypto.randomUUID(),
    actor: options.actor ?? "operator-current",
    reason: options.reason ?? "verified local test transition",
    from_state: current.state,
    to_state: to,
    expected_generation: current.generation,
    expected_sequence: current.sequence,
    expected_state_version: current.state_version,
    sequence: current.sequence + 1,
    generation: current.generation + (opens ? 1 : 0),
    state_version: current.state_version + 1,
    reconciliation_id: opens
      ? (options.reconciliationId === undefined ? "reconciliation:test-001" : options.reconciliationId)
      : null,
    issued_at: Math.floor(Date.now() / 1000),
  };
}

async function adminRequest(
  command: TransitionCommand,
  nonce = crypto.randomUUID().replaceAll("-", "") + "0000000000000000",
  keyId = "operator-current",
  signingKey = adminKeyPair.privateKey,
): Promise<Request> {
  const rawBody = JSON.stringify(command);
  const timestamp = Math.floor(Date.now() / 1000);
  const bodyHash = await sha256Hex(rawBody);
  const canonical = `POST\nhttps://control.internal\n/internal/admin/transitions\n${keyId}\n${timestamp}\n${nonce}\n${bodyHash}`;
  const signature = await signEd25519(signingKey, canonical);
  return new Request("https://control.internal/internal/admin/transitions", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-recovery-admin-key-id": keyId,
      "x-recovery-admin-timestamp": String(timestamp),
      "x-recovery-admin-nonce": nonce,
      "x-recovery-admin-signature": signature,
    },
    body: rawBody,
  });
}

async function invoke(request: unknown, testEnv: Env): Promise<Response> {
  return fetchHandler(request as Parameters<typeof fetchHandler>[0], testEnv);
}

async function invokeAdmin(request: unknown, testEnv: Env): Promise<Response> {
  return handleAdminTransition(request as Parameters<typeof handleAdminTransition>[0], testEnv);
}

describe("Recovery Fence Worker / SQLite Durable Object", () => {
  it("starts closed and returns only a signed, nonce-bound status", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const response = await invoke(await authorizedRead(), testEnv);
    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body).toMatchObject({ state: "closed", initialized: false, verified: true, sequence: 0, generation: 0, state_version: 0 });
    expect(typeof body.request_nonce).toBe("string");
  });

  it("does not expose a state-changing public route", async () => {
    const testEnv = workerEnv(new ProtocolLedger(ledgerKeyPair.privateKey));
    expect((await invoke(new Request("https://recovery.test/v1/recovery", { method: "POST" }), testEnv)).status).toBe(404);
    expect((await invoke(new Request("https://recovery.test/v1/admin/transitions"), testEnv)).status).toBe(404);
    expect((await invoke(new Request("https://recovery.test/internal/admin/transitions", { method: "POST" }), testEnv)).status).toBe(404);
    expect((await invoke(new Request("https://recovery.test/internal/admin/reconcile", { method: "POST" }), testEnv)).status).toBe(404);
    expect((await invoke(new Request("https://recovery.test/"), testEnv)).status).toBe(404);
  });

  it("fails closed when no independent ledger binding exists", async () => {
    const testEnv = workerEnv(new ProtocolLedger(ledgerKeyPair.privateKey));
    delete testEnv.EVENT_LEDGER;
    const response = await invoke(await authorizedRead(), testEnv);
    expect(response.status).toBe(503);
  });

  it("rejects missing, invalid, and replayed read authentication", async () => {
    const testEnv = workerEnv(new ProtocolLedger(ledgerKeyPair.privateKey));
    expect((await invoke(readRequest(), testEnv)).status).toBe(401);
    const invalid = await authorizedRead(readRequest(), "reader-current", "wrong-key-that-is-over-32-characters");
    expect((await invoke(invalid, testEnv)).status).toBe(401);
    const signed = await authorizedRead();
    expect((await invoke(signed.clone(), testEnv)).status).toBe(200);
    expect((await invoke(signed.clone(), testEnv)).status).toBe(401);
  });

  it("accepts the previous read key only during the configured rotation window", async () => {
    const testEnv = workerEnv(new ProtocolLedger(ledgerKeyPair.privateKey));
    const signed = await authorizedRead(readRequest(), "reader-previous", "local-only-old-recovery-reader-secret-32bytes");
    expect((await invoke(signed, testEnv)).status).toBe(200);
    const retired = await authorizedRead(readRequest(), "reader-retired", READ_SECRET);
    expect((await invoke(retired, testEnv)).status).toBe(401);
  });

  it("keeps read and administrator identities separate", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const command = await commandFor(ledger.current, "reconciling");
    command.actor = "operator-impostor";
    expect((await invokeAdmin(await adminRequest(command), testEnv)).status).toBe(403);
    expect(ledger.current.sequence).toBe(0);

    command.actor = "operator-current";
    const wrongIdentity = await adminRequest(command, undefined, "reader-current");
    expect((await invokeAdmin(wrongIdentity, testEnv)).status).toBe(401);
    expect(ledger.current.sequence).toBe(0);
    ledger.unavailableAppend = true;
    expect((await invokeAdmin(await adminRequest(command), testEnv)).status).toBe(503);
    expect(ledger.current.sequence).toBe(0);
  });

  it("accepts previous administrator and ledger verification keys during rotation", async () => {
    const ledger = new ProtocolLedger(previousLedgerKeyPair.privateKey, "ledger-previous");
    const testEnv = workerEnv(ledger);
    const read = await invoke(await authorizedRead(), testEnv);
    expect(read.status).toBe(200);
    const command = await commandFor(ledger.current, "reconciling", { actor: "operator-previous" });
    const update = await invokeAdmin(
      await adminRequest(command, undefined, "operator-previous", previousAdminKeyPair.privateKey),
      testEnv,
    );
    expect(update.status).toBe(200);
  });

  it("denies on ledger timeout, missing history, or invalid signatures", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    ledger.unavailableHead = true;
    expect((await invoke(await authorizedRead(), testEnv)).status).toBe(503);
    ledger.unavailableHead = false;
    ledger.corruptHead = true;
    expect((await invoke(await authorizedRead(), testEnv)).status).toBe(503);
  });

  it("requires closed → reconciling → explicitly reconciled open", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const directOpen = await commandFor(ledger.current, "open");
    expect((await invokeAdmin(await adminRequest(directOpen), testEnv)).status).toBe(409);
    expect(ledger.current.sequence).toBe(0);

    const enter = await commandFor(ledger.current, "reconciling");
    expect((await invokeAdmin(await adminRequest(enter), testEnv)).status).toBe(200);
    const open = await commandFor(ledger.current, "open", { reconciliationId: "verified-restore-watermark-1" });
    expect((await invokeAdmin(await adminRequest(open), testEnv)).status).toBe(200);
    expect((await (await invoke(await authorizedRead(), testEnv)).json() as Record<string, unknown>).state).toBe("open");
    expect(ledger.current).toMatchObject({ state: "open", generation: 1, sequence: 2, state_version: 2 });
  });

  it("requires actor, reason, expected versions, and rejects stale generation or sequence", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const stale = await commandFor(ledger.current, "reconciling");
    stale.expected_sequence = 9;
    stale.sequence = 10;
    expect((await invokeAdmin(await adminRequest(stale), testEnv)).status).toBe(409);
    const wrongGeneration = await commandFor(ledger.current, "reconciling");
    wrongGeneration.generation = 3;
    expect((await invokeAdmin(await adminRequest(wrongGeneration), testEnv)).status).toBe(409);
    const noReason = await commandFor(ledger.current, "reconciling");
    noReason.reason = "";
    expect((await invokeAdmin(await adminRequest(noReason), testEnv)).status).toBe(409);
    expect(ledger.current.sequence).toBe(0);
  });

  it("makes repeated event IDs idempotent and rejects changed payloads", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const eventId = "event-idempotent-1";
    const command = await commandFor(ledger.current, "reconciling", { eventId });
    const signedRequest = await adminRequest(command);
    const first = await invokeAdmin(signedRequest.clone(), testEnv);
    expect(first.status).toBe(200);
    expect((await invokeAdmin(signedRequest.clone(), testEnv)).status).toBe(409);
    const duplicate = await invokeAdmin(await adminRequest(command), testEnv);
    expect(duplicate.status).toBe(200);
    expect((await duplicate.json() as Record<string, unknown>).idempotent).toBe(true);
    expect(ledger.current.sequence).toBe(1);

    const changed = { ...command, reason: "changed payload" };
    expect((await invokeAdmin(await adminRequest(changed), testEnv)).status).toBe(409);
    expect(ledger.current.sequence).toBe(1);
  });

  it("serializes competing writes and permits exactly one sequence winner", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const a = await commandFor(ledger.current, "reconciling", { eventId: "racing-event-a" });
    const b = await commandFor(ledger.current, "reconciling", { eventId: "racing-event-b" });
    const responses = await Promise.all([
      adminRequest(a).then((request) => invokeAdmin(request, testEnv)),
      adminRequest(b).then((request) => invokeAdmin(request, testEnv)),
    ]);
    expect(responses.filter((response) => response.status === 200)).toHaveLength(1);
    expect(responses.some((response) => response.status === 409 || response.status === 503)).toBe(true);
    expect(ledger.current.sequence).toBe(1);
    const status = await invoke(await authorizedRead(), testEnv);
    expect(status.status).toBe(200);
    expect((await status.json() as Record<string, unknown>).sequence).toBe(1);
  });

  it("denies reads when a restored old DO snapshot trails the independent ledger head", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    ledger.simulateAuthoritativeHeadAhead();
    const response = await invoke(await authorizedRead(), testEnv);
    expect(response.status).toBe(503);
  });

  it("replays the signed event chain into a stale DO only after the authoritative gate is closed", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const writerEnv = workerEnv(ledger, "restore-writer");
    const enter = await commandFor(ledger.current, "reconciling");
    expect((await invokeAdmin(await adminRequest(enter), writerEnv)).status).toBe(200);
    const open = await commandFor(ledger.current, "open", { reconciliationId: "verified-restore-watermark-1" });
    expect((await invokeAdmin(await adminRequest(open), writerEnv)).status).toBe(200);

    const restoredEnv = workerEnv(ledger, "restored-old-do");
    expect((await invoke(await authorizedRead(), restoredEnv)).status).toBe(503);
    const close = await commandFor(ledger.current, "closed", { reason: "close before restore reconciliation" });
    const closeResponse = await invokeAdmin(await adminRequest(close), restoredEnv);
    expect(closeResponse.status).toBe(200);
    expect(await closeResponse.json()).toMatchObject({ state: "closed", sequence: 3, replayed_events: 3 });

    const verified = await invoke(await authorizedRead(), restoredEnv);
    expect(verified.status).toBe(200);
    expect(await verified.json()).toMatchObject({ state: "closed", sequence: 3 });
  });

  it("leaves a stale DO denied when the signed replay history has a gap or bad signature", async () => {
    for (const corrupt of ["gap", "signature"] as const) {
      const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
      const writerEnv = workerEnv(ledger, `restore-source-${corrupt}`);
      const enter = await commandFor(ledger.current, "reconciling");
      await invokeAdmin(await adminRequest(enter), writerEnv);
      const open = await commandFor(ledger.current, "open", { reconciliationId: "verified-restore-watermark-1" });
      await invokeAdmin(await adminRequest(open), writerEnv);

      const restoredEnv = workerEnv(ledger, `restore-target-${corrupt}`);
      if (corrupt === "gap") ledger.missingEventSequence = 2;
      else ledger.corruptEventSequence = 2;
      const close = await commandFor(ledger.current, "closed", { reason: "close before restore reconciliation" });
      expect((await invokeAdmin(await adminRequest(close), restoredEnv)).status).toBe(503);
      expect((await invoke(await authorizedRead(), restoredEnv)).status).toBe(503);
    }
  });

  it("does not rewind a DO or trust a ledger that has lost events", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const writerEnv = workerEnv(ledger, "ahead-do-writer");
    const enter = await commandFor(ledger.current, "reconciling");
    await invokeAdmin(await adminRequest(enter), writerEnv);
    const open = await commandFor(ledger.current, "open", { reconciliationId: "verified-restore-watermark-1" });
    await invokeAdmin(await adminRequest(open), writerEnv);

    // Model a ledger restore that lost its signed tail while the DO remained ahead.
    ledger.current = {
      state: "closed",
      generation: 0,
      sequence: 0,
      state_version: 0,
      event_id: null,
      event_hash: "0".repeat(64),
    };
    ledger.events = [];
    ledger.receipts.clear();

    const closeOrReconcile = await commandFor(ledger.current, "reconciling");
    const response = await invokeAdmin(await adminRequest(closeOrReconcile), writerEnv);
    expect(response.status).toBe(503);
    expect((await invoke(await authorizedRead(), writerEnv)).status).toBe(503);
  });

  it("fails closed if the ledger append acknowledgement is invalid or disappears", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    ledger.corruptNextReceipt = true;
    const command = await commandFor(ledger.current, "reconciling");
    expect((await invokeAdmin(await adminRequest(command), testEnv)).status).toBe(503);
    expect(ledger.current.sequence).toBe(1);
    const read = await invoke(await authorizedRead(), testEnv);
    expect(read.status).toBe(503);
  });

  it("keeps the open transition denied when its signed reconciliation ID is absent", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const enter = await commandFor(ledger.current, "reconciling");
    expect((await invokeAdmin(await adminRequest(enter), testEnv)).status).toBe(200);
    const open = await commandFor(ledger.current, "open", { reconciliationId: null });
    expect((await invokeAdmin(await adminRequest(open), testEnv)).status).toBe(409);
    expect(ledger.current.state).toBe("reconciling");
  });

  it("does not treat an arbitrary non-empty value as reconciliation evidence", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const enter = await commandFor(ledger.current, "reconciling");
    expect((await invokeAdmin(await adminRequest(enter), testEnv)).status).toBe(200);
    const open = await commandFor(ledger.current, "open", { reconciliationId: "invented-proof" });
    expect((await invokeAdmin(await adminRequest(open), testEnv)).status).toBe(503);
    expect(ledger.current.state).toBe("reconciling");
    const status = await invoke(await authorizedRead(), testEnv);
    expect(status.status).toBe(200);
    expect((await status.json() as Record<string, unknown>).state).toBe("reconciling");
  });

  it("returns to a verified closed state after a valid close event", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const enter = await commandFor(ledger.current, "reconciling");
    await invokeAdmin(await adminRequest(enter), testEnv);
    const open = await commandFor(ledger.current, "open");
    await invokeAdmin(await adminRequest(open), testEnv);
    const close = await commandFor(ledger.current, "closed");
    expect((await invokeAdmin(await adminRequest(close), testEnv)).status).toBe(200);
    const read = await invoke(await authorizedRead(), testEnv);
    expect(read.status).toBe(200);
    expect((await read.json() as Record<string, unknown>).state).toBe("closed");
  });

  it("measures local singleton queue latency under concurrent reads", async () => {
    const ledger = new ProtocolLedger(ledgerKeyPair.privateKey);
    const testEnv = workerEnv(ledger);
    const count = 60;
    const started = performance.now();
    const samples = await Promise.all(
      Array.from({ length: count }, async () => {
        const requestStarted = performance.now();
        const response = await invoke(await authorizedRead(), testEnv);
        return { response, latencyMs: performance.now() - requestStarted };
      }),
    );
    const elapsedMs = performance.now() - started;
    const sorted = samples.map((sample) => sample.latencyMs).sort((a, b) => a - b);
    const percentile = (fraction: number) => sorted[Math.ceil(sorted.length * fraction) - 1];
    expect(samples.every((sample) => sample.response.status === 200)).toBe(true);
    expect(Number.isFinite(percentile(0.95))).toBe(true);
    console.info(
      `Local Miniflare singleton: ${count} concurrent reads completed in ${elapsedMs.toFixed(1)} ms; aggregate ${((count * 1000) / elapsedMs).toFixed(1)} req/s; p50 ${percentile(0.5).toFixed(1)} ms, p95 ${percentile(0.95).toFixed(1)} ms, p99 ${percentile(0.99).toFixed(1)} ms (harness only).`,
    );
  });
});
