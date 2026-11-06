import type {
  Env,
  LedgerHead,
  RecoverySnapshot,
} from "./types";
import {
  parseReadAuthorization,
  verifyLedgerHead,
  verifyReadAuthorization,
} from "./crypto";
import { RecoveryState } from "./state";

export { RecoveryState };

const READ_PATH = "/v1/recovery";

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

async function ledgerFetch(env: Env, path: string, init?: RequestInit): Promise<Response> {
  if (!env.EVENT_LEDGER) throw new Error("ledger unavailable");
  return env.EVENT_LEDGER.fetch(`https://recovery-ledger.internal${path}`, init);
}

async function readStatus(request: Request, env: Env): Promise<Response> {
  const auth = parseReadAuthorization(request);
  if (!auth || !(await verifyReadAuthorization(auth, request, env))) return json({ error: "unauthorized" }, 401);

  try {
    const state = singleton(env);
    const read = await state.readSnapshotWithNonce(auth.nonce, Math.floor(Date.now() / 1000));
    if (!read.accepted) return json({ error: "unauthorized" }, 401);
    const snapshot = read.snapshot;
    const headResponse = await ledgerFetch(env, `/v1/head?nonce=${encodeURIComponent(auth.nonce)}`, {
      headers: { "x-recovery-request-nonce": auth.nonce, "cache-control": "no-store" },
    });
    if (!headResponse.ok) throw new Error("ledger unavailable");
    const head = (await headResponse.json()) as LedgerHead;
    if (!(await verifyLedgerHead(head, auth.nonce, env)) || !sameHead(snapshot, head)) {
      return json({ error: "recovery unavailable" }, 503);
    }
    return json({
      state: snapshot.state,
      generation: snapshot.generation,
      sequence: snapshot.sequence,
      state_version: snapshot.state_version,
      initialized: snapshot.initialized,
      verified: true,
      request_nonce: auth.nonce,
      checkpoint_id: head.checkpoint_id,
    });
  } catch {
    return json({ error: "recovery unavailable" }, 503);
  }
}

function sameHead(snapshot: RecoverySnapshot, head: LedgerHead): boolean {
  return snapshot.state === head.state &&
    snapshot.generation === head.generation &&
    snapshot.sequence === head.sequence &&
    snapshot.state_version === head.state_version &&
    snapshot.last_event_id === head.event_id &&
    snapshot.last_event_hash === head.event_hash;
}

export async function fetchHandler(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === READ_PATH) return readStatus(request, env);
  return json({ error: "not found" }, 404);
}

export default { fetch: fetchHandler };
