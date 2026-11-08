import type { RecoveryState } from "./state";

export type RecoveryStateName = "closed" | "reconciling" | "open";

export interface RecoverySnapshot {
  state: RecoveryStateName;
  initialized: boolean;
  generation: number;
  sequence: number;
  state_version: number;
  last_event_id: string | null;
  last_event_hash: string;
  updated_at: string | null;
}

export interface TransitionCommand {
  event_id: string;
  actor: string;
  reason: string;
  from_state: RecoveryStateName;
  to_state: RecoveryStateName;
  expected_generation: number;
  expected_sequence: number;
  expected_state_version: number;
  sequence: number;
  generation: number;
  state_version: number;
  reconciliation_id: string | null;
  issued_at: number;
}

export interface LedgerReceipt extends TransitionCommand {
  durable: true;
  payload_hash: string;
  previous_event_hash: string;
  event_hash: string;
  ledger_event_id: string;
  signing_key_id: string;
  signature: string;
}

export interface LedgerHead {
  state: RecoveryStateName;
  generation: number;
  sequence: number;
  state_version: number;
  event_id: string | null;
  event_hash: string;
  checkpoint_id: string;
  signing_key_id: string;
  issued_at: number;
  request_nonce: string;
  signature: string;
}

export interface LedgerEventPage {
  after_sequence: number;
  through_sequence: number;
  events: LedgerReceipt[];
}

export interface Env {
  RECOVERY_STATE: DurableObjectNamespace<RecoveryState>;
  LEDGER_STATE?: DurableObjectNamespace;
  EVENT_LEDGER?: Fetcher;
  READ_KEY_ID?: string;
  READ_KEY_SECRET?: string;
  READ_PREVIOUS_KEY_ID?: string;
  READ_PREVIOUS_KEY_SECRET?: string;
  ADMIN_KEY_ID?: string;
  ADMIN_PUBLIC_KEY?: string;
  ADMIN_PREVIOUS_KEY_ID?: string;
  ADMIN_PREVIOUS_PUBLIC_KEY?: string;
  LEDGER_KEY_ID?: string;
  LEDGER_PUBLIC_KEY?: string;
  LEDGER_PREVIOUS_KEY_ID?: string;
  LEDGER_PREVIOUS_PUBLIC_KEY?: string;
  LEDGER_PRIVATE_KEY?: string;
  RESTORE_TEST_KEY?: string;
  CLOCK_SKEW_SECONDS?: string;
}

export interface ReadAuthorization {
  keyId: string;
  timestamp: number;
  nonce: string;
  signature: string;
}

export interface AdminAuthorization extends ReadAuthorization {}

export type TransitionResult =
  | { ok: true; snapshot: RecoverySnapshot; idempotent: boolean }
  | { ok: false; code: "conflict" | "invalid" | "storage" };

export class TransitionRejected extends Error {
  constructor(readonly code: "conflict" | "invalid" | "storage") {
    super(code);
  }
}
