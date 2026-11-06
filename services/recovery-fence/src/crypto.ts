import type {
  AdminAuthorization,
  Env,
  LedgerHead,
  LedgerReceipt,
  ReadAuthorization,
  TransitionCommand,
} from "./types";

const encoder = new TextEncoder();
const MAX_CLOCK_SKEW_SECONDS = 30;

function decodeBase64Url(value: string): Uint8Array {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

function encodeBase64Url(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function canonical(fields: readonly (string | number | null)[]): string {
  return fields.map((field) => (field === null ? "" : String(field))).join("\n");
}

export function readCanonical(
  origin: string,
  path: string,
  keyId: string,
  timestamp: number,
  nonce: string,
): string {
  return canonical(["GET", origin, path, keyId, timestamp, nonce]);
}

export async function adminCanonical(
  origin: string,
  path: string,
  keyId: string,
  timestamp: number,
  nonce: string,
  body: string,
): Promise<string> {
  return canonical(["POST", origin, path, keyId, timestamp, nonce, await sha256Hex(body)]);
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value) as BufferSource);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function ledgerEventHash(previousEventHash: string, payloadHash: string): Promise<string> {
  if (!/^[a-f0-9]{64}$/u.test(previousEventHash) || !/^[a-f0-9]{64}$/u.test(payloadHash)) {
    throw new Error("invalid ledger hash");
  }
  return sha256Hex(`evidence-recovery-event-v1\n${previousEventHash}\n${payloadHash}`);
}

async function hmacValid(secret: string, payload: string, signature: string): Promise<boolean> {
  if (secret.length < 32) return false;
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );
    return crypto.subtle.verify(
      "HMAC",
      key,
      decodeBase64Url(signature) as BufferSource,
      encoder.encode(payload) as BufferSource,
    );
  } catch {
    return false;
  }
}

async function ed25519Valid(publicKey: string, payload: string, signature: string): Promise<boolean> {
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      decodeBase64Url(publicKey) as BufferSource,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    return crypto.subtle.verify(
      "Ed25519",
      key,
      decodeBase64Url(signature) as BufferSource,
      encoder.encode(payload) as BufferSource,
    );
  } catch {
    return false;
  }
}

export function parseReadAuthorization(request: Request): ReadAuthorization | null {
  const keyId = request.headers.get("x-recovery-key-id") ?? "";
  const timestamp = Number(request.headers.get("x-recovery-timestamp"));
  const nonce = request.headers.get("x-recovery-nonce") ?? "";
  const signature = request.headers.get("x-recovery-signature") ?? "";
  if (!keyId || !Number.isSafeInteger(timestamp) || !/^[A-Za-z0-9_-]{22,128}$/u.test(nonce) || !signature) {
    return null;
  }
  return { keyId, timestamp, nonce, signature };
}

export function parseAdminAuthorization(request: Request): AdminAuthorization | null {
  const keyId = request.headers.get("x-recovery-admin-key-id") ?? "";
  const timestamp = Number(request.headers.get("x-recovery-admin-timestamp"));
  const nonce = request.headers.get("x-recovery-admin-nonce") ?? "";
  const signature = request.headers.get("x-recovery-admin-signature") ?? "";
  if (!keyId || !Number.isSafeInteger(timestamp) || !/^[A-Za-z0-9_-]{22,128}$/u.test(nonce) || !signature) {
    return null;
  }
  return { keyId, timestamp, nonce, signature };
}

function withinClockSkew(timestamp: number, env: Env): boolean {
  const configured = Number(env.CLOCK_SKEW_SECONDS ?? MAX_CLOCK_SKEW_SECONDS);
  const allowed = Number.isInteger(configured) && configured > 0 && configured <= 120
    ? configured
    : MAX_CLOCK_SKEW_SECONDS;
  return Math.abs(Math.floor(Date.now() / 1000) - timestamp) <= allowed;
}

export async function verifyReadAuthorization(
  auth: ReadAuthorization & { signature?: string },
  request: Request,
  env: Env,
): Promise<boolean> {
  if (!auth.signature || !withinClockSkew(auth.timestamp, env)) return false;
  const secret = auth.keyId === env.READ_KEY_ID
    ? env.READ_KEY_SECRET
    : auth.keyId === env.READ_PREVIOUS_KEY_ID
      ? env.READ_PREVIOUS_KEY_SECRET
      : undefined;
  if (!secret) return false;
  const url = new URL(request.url);
  return hmacValid(
    secret,
    readCanonical(url.origin, url.pathname, auth.keyId, auth.timestamp, auth.nonce),
    auth.signature,
  );
}

export async function verifyAdminAuthorization(
  auth: AdminAuthorization & { signature?: string },
  request: Request,
  env: Env,
  rawBody: string,
): Promise<boolean> {
  if (!auth.signature || !withinClockSkew(auth.timestamp, env)) return false;
  const publicKey = auth.keyId === env.ADMIN_KEY_ID
    ? env.ADMIN_PUBLIC_KEY
    : auth.keyId === env.ADMIN_PREVIOUS_KEY_ID
      ? env.ADMIN_PREVIOUS_PUBLIC_KEY
      : undefined;
  if (!publicKey) return false;
  return ed25519Valid(
    publicKey,
    await adminCanonical(
      new URL(request.url).origin,
      new URL(request.url).pathname,
      auth.keyId,
      auth.timestamp,
      auth.nonce,
      rawBody,
    ),
    auth.signature,
  );
}

export function receiptCanonical(receipt: LedgerReceipt): string {
  return canonical([
    "evidence-recovery-ledger-receipt-v1",
    receipt.signing_key_id,
    receipt.ledger_event_id,
    receipt.event_id,
    receipt.payload_hash,
    receipt.actor,
    receipt.reason,
    receipt.from_state,
    receipt.to_state,
    receipt.expected_generation,
    receipt.expected_sequence,
    receipt.expected_state_version,
    receipt.sequence,
    receipt.generation,
    receipt.state_version,
    receipt.reconciliation_id,
    receipt.issued_at,
    receipt.previous_event_hash,
    receipt.event_hash,
    receipt.durable ? "true" : "false",
  ]);
}

export function headCanonical(head: LedgerHead): string {
  return canonical([
    "evidence-recovery-ledger-head-v1",
    head.signing_key_id,
    head.state,
    head.generation,
    head.sequence,
    head.state_version,
    head.event_id,
    head.event_hash,
    head.checkpoint_id,
    head.issued_at,
    head.request_nonce,
  ]);
}

export async function verifyLedgerReceipt(
  receipt: LedgerReceipt,
  env: Env,
): Promise<boolean> {
  const publicKey = receipt.signing_key_id === env.LEDGER_KEY_ID
    ? env.LEDGER_PUBLIC_KEY
    : receipt.signing_key_id === env.LEDGER_PREVIOUS_KEY_ID
      ? env.LEDGER_PREVIOUS_PUBLIC_KEY
      : undefined;
  if (
    !publicKey || receipt.durable !== true ||
    !/^[a-f0-9]{64}$/u.test(receipt.payload_hash) ||
    !/^[a-f0-9]{64}$/u.test(receipt.previous_event_hash) ||
    !/^[a-f0-9]{64}$/u.test(receipt.event_hash)
  ) return false;
  return ed25519Valid(publicKey, receiptCanonical(receipt), receipt.signature);
}

export async function verifyLedgerHead(
  head: LedgerHead,
  expectedNonce: string,
  env: Env,
): Promise<boolean> {
  const publicKey = head.signing_key_id === env.LEDGER_KEY_ID
    ? env.LEDGER_PUBLIC_KEY
    : head.signing_key_id === env.LEDGER_PREVIOUS_KEY_ID
      ? env.LEDGER_PREVIOUS_PUBLIC_KEY
      : undefined;
  if (
    !publicKey || head.request_nonce !== expectedNonce ||
    !/^[a-f0-9]{64}$/u.test(head.event_hash)
  ) return false;
  if (!Number.isSafeInteger(head.issued_at) || Math.abs(Date.now() - head.issued_at) > 30_000) return false;
  return ed25519Valid(publicKey, headCanonical(head), head.signature);
}

export function encodeSignature(signature: ArrayBuffer): string {
  return encodeBase64Url(signature);
}

export function transitionCanonical(command: TransitionCommand): string {
  return canonical([
    "evidence-recovery-transition-v1",
    command.event_id,
    command.actor,
    command.reason,
    command.from_state,
    command.to_state,
    command.expected_generation,
    command.expected_sequence,
    command.expected_state_version,
    command.sequence,
    command.generation,
    command.state_version,
    command.reconciliation_id,
    command.issued_at,
  ]);
}
