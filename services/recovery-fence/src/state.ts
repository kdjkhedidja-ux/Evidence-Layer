import { DurableObject } from "cloudflare:workers";
import type {
  Env,
  RecoverySnapshot,
  RecoveryStateName,
  TransitionCommand,
  TransitionResult,
} from "./types";
import { TransitionRejected } from "./types";
import { ledgerEventHash } from "./crypto";

interface StateRow extends Record<string, string | number | null> {
  state: RecoveryStateName;
  initialized: number;
  generation: number;
  sequence: number;
  state_version: number;
  last_event_id: string | null;
  last_event_hash: string;
  updated_at: string | null;
}

interface EventRow extends Record<string, string | number | null> {
  payload_hash: string;
  state: RecoveryStateName;
  generation: number;
  sequence: number;
  state_version: number;
  event_hash: string;
}

const MAX_EVENT_ID_LENGTH = 160;
const MAX_ACTOR_LENGTH = 160;
const MAX_REASON_LENGTH = 1000;
const NONCE_TTL_SECONDS = 180;
const GENESIS_EVENT_HASH = "0".repeat(64);

function snapshot(row: StateRow): RecoverySnapshot {
  return {
    state: row.state,
    initialized: row.initialized === 1,
    generation: row.generation,
    sequence: row.sequence,
    state_version: row.state_version,
    last_event_id: row.last_event_id,
    last_event_hash: row.last_event_hash,
    updated_at: row.updated_at,
  };
}

function isState(value: unknown): value is RecoveryStateName {
  return value === "closed" || value === "reconciling" || value === "open";
}

export class RecoveryState extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS recovery_state (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          state TEXT NOT NULL CHECK (state IN ('closed', 'reconciling', 'open')),
          initialized INTEGER NOT NULL CHECK (initialized IN (0, 1)),
          generation INTEGER NOT NULL CHECK (generation >= 0),
          sequence INTEGER NOT NULL CHECK (sequence >= 0),
          state_version INTEGER NOT NULL CHECK (state_version >= 0),
          last_event_id TEXT,
          last_event_hash TEXT NOT NULL DEFAULT '${GENESIS_EVENT_HASH}',
          updated_at TEXT
        );
        INSERT OR IGNORE INTO recovery_state
          (singleton, state, initialized, generation, sequence, state_version, last_event_id, updated_at)
          VALUES (1, 'closed', 0, 0, 0, 0, NULL, NULL);
        CREATE TABLE IF NOT EXISTS applied_events (
          event_id TEXT PRIMARY KEY,
          payload_hash TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('closed', 'reconciling', 'open')),
          generation INTEGER NOT NULL,
          sequence INTEGER NOT NULL,
          state_version INTEGER NOT NULL,
          event_hash TEXT NOT NULL DEFAULT '',
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS request_nonces (
          nonce TEXT PRIMARY KEY,
          expires_at INTEGER NOT NULL
        );
      `);
      const stateColumns = ctx.storage.sql.exec<{ name: string } & Record<string, string | number | null>>(
        "PRAGMA table_info(recovery_state)",
      ).toArray();
      if (!stateColumns.some((column) => column.name === "last_event_hash")) {
        ctx.storage.sql.exec(
          `ALTER TABLE recovery_state ADD COLUMN last_event_hash TEXT NOT NULL DEFAULT '${GENESIS_EVENT_HASH}'`,
        );
      }
      const eventColumns = ctx.storage.sql.exec<{ name: string } & Record<string, string | number | null>>(
        "PRAGMA table_info(applied_events)",
      ).toArray();
      if (!eventColumns.some((column) => column.name === "event_hash")) {
        ctx.storage.sql.exec("ALTER TABLE applied_events ADD COLUMN event_hash TEXT NOT NULL DEFAULT ''");
      }
    });
  }

  async getSnapshot(): Promise<RecoverySnapshot> {
    return this.snapshotNow();
  }

  /**
   * Test-only PITR helpers. The public worker gates these routes behind a
   * separate random secret and does not expose them when that secret is unset.
   */
  async currentBookmark(): Promise<string> {
    return this.ctx.storage.getCurrentBookmark();
  }

  async bookmarkForTime(timestamp: number): Promise<string> {
    return this.ctx.storage.getBookmarkForTime(timestamp);
  }

  async scheduleRestore(bookmark: string): Promise<string> {
    return this.ctx.storage.onNextSessionRestoreBookmark(bookmark);
  }

  async abortNow(): Promise<void> {
    this.ctx.abort();
  }

  private snapshotNow(): RecoverySnapshot {
    const row = this.ctx.storage.sql
      .exec<StateRow>("SELECT * FROM recovery_state WHERE singleton = 1")
      .toArray()[0];
    if (!row || !isState(row.state)) {
      return {
        state: "closed",
        initialized: false,
        generation: 0,
        sequence: 0,
        state_version: 0,
        last_event_id: null,
        last_event_hash: GENESIS_EVENT_HASH,
        updated_at: null,
      };
    }
    return snapshot(row);
  }

  async readSnapshotWithNonce(
    nonce: string,
    nowSeconds: number,
  ): Promise<{ snapshot: RecoverySnapshot; accepted: boolean }> {
    return this.ctx.storage.transactionSync(() => {
      const accepted = this.insertNonce(nonce, nowSeconds);
      return { snapshot: this.snapshotNow(), accepted };
    });
  }

  async prepareTransition(
    command: TransitionCommand,
    payloadHash: string,
    nonce: string,
    nowSeconds: number,
  ): Promise<TransitionResult> {
    try {
      return this.ctx.storage.transactionSync(() => {
        if (!this.insertNonce(nonce, nowSeconds)) throw new TransitionRejected("conflict");
        return this.inspectTransition(command, payloadHash);
      });
    } catch (error) {
      if (error instanceof TransitionRejected) return { ok: false, code: error.code };
      return { ok: false, code: "storage" };
    }
  }

  async applyTransition(
    command: TransitionCommand,
    payloadHash: string,
    previousEventHash: string,
    eventHash: string,
  ): Promise<TransitionResult> {
    if (await ledgerEventHash(previousEventHash, payloadHash) !== eventHash) {
      return { ok: false, code: "invalid" };
    }
    try {
      return this.ctx.storage.transactionSync(() => {
        const existing = this.ctx.storage.sql
          .exec<EventRow>("SELECT * FROM applied_events WHERE event_id = ?", command.event_id)
          .toArray()[0];
        if (existing) {
          if (existing.payload_hash !== payloadHash) throw new TransitionRejected("conflict");
          if (existing.event_hash !== eventHash) throw new TransitionRejected("conflict");
          return { ok: true, snapshot: this.snapshotNow(), idempotent: true } as const;
        }

        const current = this.currentRow();
        if (current.last_event_hash !== previousEventHash) throw new TransitionRejected("conflict");
        this.validateTransition(current, command);
        const updatedAt = new Date().toISOString();

        this.ctx.storage.sql.exec(
          `UPDATE recovery_state
           SET state = ?, initialized = 1, generation = ?, sequence = ?, state_version = ?,
               last_event_id = ?, last_event_hash = ?, updated_at = ?
           WHERE singleton = 1`,
          command.to_state,
          command.generation,
          command.sequence,
          command.state_version,
          command.event_id,
          eventHash,
          updatedAt,
        );
        this.ctx.storage.sql.exec(
          `INSERT INTO applied_events
             (event_id, payload_hash, state, generation, sequence, state_version, event_hash, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
          command.event_id,
          payloadHash,
          command.to_state,
          command.generation,
          command.sequence,
          command.state_version,
          eventHash,
          updatedAt,
        );
        return { ok: true, snapshot: this.snapshotNow(), idempotent: false } as const;
      });
    } catch (error) {
      if (error instanceof TransitionRejected) return { ok: false, code: error.code };
      return { ok: false, code: "storage" };
    }
  }

  private inspectTransition(command: TransitionCommand, payloadHash: string): TransitionResult {
    const existing = this.ctx.storage.sql
      .exec<EventRow>("SELECT * FROM applied_events WHERE event_id = ?", command.event_id)
      .toArray()[0];
    if (existing) {
      if (existing.payload_hash !== payloadHash) throw new TransitionRejected("conflict");
      return { ok: true, snapshot: this.snapshotNow(), idempotent: true };
    }
    this.validateTransition(this.currentRow(), command);
    return { ok: true, snapshot: this.snapshotNow(), idempotent: false };
  }

  private insertNonce(nonce: string, nowSeconds: number): boolean {
    if (!/^[A-Za-z0-9_-]{22,128}$/u.test(nonce)) return false;
    this.ctx.storage.sql.exec("DELETE FROM request_nonces WHERE expires_at < ?", nowSeconds);
    try {
      this.ctx.storage.sql.exec(
        "INSERT INTO request_nonces (nonce, expires_at) VALUES (?, ?)",
        nonce,
        nowSeconds + NONCE_TTL_SECONDS,
      );
      return true;
    } catch {
      return false;
    }
  }

  private currentRow(): StateRow {
    const row = this.ctx.storage.sql
      .exec<StateRow>("SELECT * FROM recovery_state WHERE singleton = 1")
      .toArray()[0];
    if (!row || !isState(row.state)) throw new TransitionRejected("storage");
    return row;
  }

  private validateTransition(current: StateRow, command: TransitionCommand): void {
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
    ) {
      throw new TransitionRejected("conflict");
    }

    const opens = command.to_state === "open";
    const allowed =
      (current.state === "closed" && command.to_state === "reconciling") ||
      (current.state === "reconciling" && (command.to_state === "closed" || opens)) ||
      (current.state === "open" && command.to_state === "closed");
    if (!allowed) throw new TransitionRejected("invalid");

    if (opens) {
      if (
        command.generation !== current.generation + 1 ||
        !command.reconciliation_id ||
        command.reconciliation_id.length > MAX_EVENT_ID_LENGTH
      ) throw new TransitionRejected("invalid");
    } else if (
      command.generation !== current.generation ||
      command.reconciliation_id !== null
    ) {
      throw new TransitionRejected("invalid");
    }
  }
}
