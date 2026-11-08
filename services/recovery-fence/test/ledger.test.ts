import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import type { Env, LedgerHead, LedgerReceipt, TransitionCommand } from "../src/types";
import {
  sha256Hex,
  transitionCanonical,
  verifyLedgerHead,
  verifyLedgerReceipt,
} from "../src/crypto";

function base64url(data: ArrayBuffer | Uint8Array): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

let enabled = false;

beforeAll(async () => {
  if (!(env as unknown as Env).LEDGER_STATE) return;
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateKey = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const publicKey = await crypto.subtle.exportKey("raw", pair.publicKey);
  const testEnv = env as unknown as Env;
  testEnv.LEDGER_KEY_ID = "ledger-current";
  testEnv.LEDGER_PRIVATE_KEY = base64url(privateKey);
  testEnv.LEDGER_PUBLIC_KEY = base64url(publicKey);
  enabled = true;
});

function command(overrides: Partial<TransitionCommand> = {}): TransitionCommand {
  return {
    event_id: `event-${crypto.randomUUID()}`,
    actor: "admin:test",
    reason: "synthetic test",
    from_state: "closed",
    to_state: "reconciling",
    expected_generation: 0,
    expected_sequence: 0,
    expected_state_version: 0,
    sequence: 1,
    generation: 0,
    state_version: 1,
    reconciliation_id: null,
    issued_at: Math.floor(Date.now() / 1000),
    ...overrides,
  };
}

function namespace() {
  const value = (env as Env).LEDGER_STATE;
  if (!value) throw new Error("LEDGER_STATE binding missing");
  return value;
}

async function append(cmd: TransitionCommand) {
  const payload = await sha256Hex(transitionCanonical(cmd));
  return namespace()
    .get(namespace().idFromName(`ledger-${crypto.randomUUID()}`))
    .fetch("https://ledger.test/v1/events/append", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: cmd, payload_hash: payload }),
    });
}

async function headFor(stubName: string, nonce: string): Promise<{ response: Response; head: LedgerHead }> {
  const response = await namespace().get(namespace().idFromName(stubName)).fetch(
    `https://ledger.test/v1/head?nonce=${encodeURIComponent(nonce)}`,
    { headers: { "x-recovery-request-nonce": nonce } },
  );
  return { response, head: await response.json() as LedgerHead };
}

describe("LedgerState Durable Object", () => {
  it("starts closed with a signed fresh head", async () => {
    if (!enabled) return;
    const nonce = `${crypto.randomUUID().replaceAll("-", "")}`.repeat(2);
    const { response, head } = await headFor(`ledger-${crypto.randomUUID()}`, nonce);
    expect(response.status).toBe(200);
    expect(head.state).toBe("closed");
    expect(head.sequence).toBe(0);
    expect(head.generation).toBe(0);
    expect(await verifyLedgerHead(head, nonce, env as Env)).toBe(true);
  });

  it("appends one durable signed event and advances the head", async () => {
    if (!enabled) return;
    const name = `ledger-${crypto.randomUUID()}`;
    const cmd = command();
    const payload = await sha256Hex(transitionCanonical(cmd));
    const response = await namespace().get(namespace().idFromName(name)).fetch(
      "https://ledger.test/v1/events/append",
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ command: cmd, payload_hash: payload }) },
    );
    expect(response.status).toBe(200);
    const receipt = await response.json() as LedgerReceipt;
    expect(receipt.durable).toBe(true);
    expect(receipt.sequence).toBe(1);
    expect(await verifyLedgerReceipt(receipt, env as Env)).toBe(true);

    const nonce = `${crypto.randomUUID().replaceAll("-", "")}`.repeat(2);
    const { head } = await headFor(name, nonce);
    expect(head.state).toBe("reconciling");
    expect(head.sequence).toBe(1);
    expect(head.event_hash).toBe(receipt.event_hash);
  });

  it("is idempotent for the same payload and rejects a conflicting reuse", async () => {
    if (!enabled) return;
    const name = `ledger-${crypto.randomUUID()}`;
    const stub = namespace().get(namespace().idFromName(name));
    const cmd = command();
    const payload = await sha256Hex(transitionCanonical(cmd));
    const body = JSON.stringify({ command: cmd, payload_hash: payload });
    const first = await stub.fetch("https://ledger.test/v1/events/append", { method: "POST", body, headers: { "content-type": "application/json" } });
    const firstReceipt = await first.json() as LedgerReceipt;
    const replay = await stub.fetch("https://ledger.test/v1/events/append", { method: "POST", body, headers: { "content-type": "application/json" } });
    expect(replay.status).toBe(200);
    expect((await replay.json() as LedgerReceipt).event_hash).toBe(firstReceipt.event_hash);

    const conflicting = command({ event_id: cmd.event_id, reason: "changed payload" });
    const conflictingPayload = await sha256Hex(transitionCanonical(conflicting));
    const conflict = await stub.fetch("https://ledger.test/v1/events/append", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: conflicting, payload_hash: conflictingPayload }),
    });
    expect(conflict.status).toBe(409);
  });

  it("rejects a second event for the same sequence and enforces transitions", async () => {
    if (!enabled) return;
    const name = `ledger-${crypto.randomUUID()}`;
    const stub = namespace().get(namespace().idFromName(name));
    const first = command();
    const firstPayload = await sha256Hex(transitionCanonical(first));
    expect((await stub.fetch("https://ledger.test/v1/events/append", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: first, payload_hash: firstPayload }),
    })).status).toBe(200);

    const sameSequence = command({ sequence: 1 });
    const sameSequencePayload = await sha256Hex(transitionCanonical(sameSequence));
    expect((await stub.fetch("https://ledger.test/v1/events/append", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: sameSequence, payload_hash: sameSequencePayload }),
    })).status).toBe(409);

    const illegalOpen = command({
      from_state: "reconciling", to_state: "open", expected_sequence: 1,
      expected_state_version: 1, sequence: 2, state_version: 2,
    });
    const illegalOpenPayload = await sha256Hex(transitionCanonical(illegalOpen));
    expect((await stub.fetch("https://ledger.test/v1/events/append", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: illegalOpen, payload_hash: illegalOpenPayload }),
    })).status).toBe(409);

    const open = command({
      from_state: "reconciling", to_state: "open", expected_sequence: 1,
      expected_state_version: 1, sequence: 2, generation: 1, state_version: 2,
      reconciliation_id: "recon:test-1",
    });
    const openPayload = await sha256Hex(transitionCanonical(open));
    expect((await stub.fetch("https://ledger.test/v1/events/append", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: open, payload_hash: openPayload }),
    })).status).toBe(200);

    const page = await stub.fetch("https://ledger.test/v1/events?after_sequence=0&through_sequence=2&limit=10");
    expect(page.status).toBe(200);
    const body = await page.json() as { events: LedgerReceipt[] };
    expect(body.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(body.events[1].generation).toBe(1);
  });
});
