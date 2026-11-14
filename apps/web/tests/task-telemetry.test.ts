import { beforeEach, describe, expect, it } from "vitest";

import {
  clearTaskTelemetry,
  readTaskTelemetry,
  recordTaskTelemetry,
  summarizeTaskTelemetry,
  type TaskContextTelemetry,
} from "../lib/task-telemetry";

const run = (overrides: Partial<TaskContextTelemetry> = {}): TaskContextTelemetry => ({
  recordedAt: "2026-11-14T00:00:00.000Z",
  status: "ok",
  latencyMs: 120,
  usedTokens: 3000,
  usedChars: 12000,
  selectedCount: 7,
  chunkCount: 264,
  matchedChunkCount: 18,
  relationshipCount: 1,
  scoringProvider: "lexical_stopword_filtered",
  fallbackReason: null,
  topK: 8,
  maxTokens: 3500,
  maxChars: 14000,
  useJev: false,
  ...overrides,
});

describe("task telemetry store", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("records newest first, caps history, and clears", () => {
    recordTaskTelemetry(run({ recordedAt: "2026-11-14T00:00:00.000Z", usedTokens: 1000 }));
    recordTaskTelemetry(run({ recordedAt: "2026-11-14T01:00:00.000Z", usedTokens: 2000 }));
    const rows = readTaskTelemetry();
    expect(rows).toHaveLength(2);
    expect(rows[0].usedTokens).toBe(2000);
    clearTaskTelemetry();
    expect(readTaskTelemetry()).toEqual([]);
  });

  it("summarizes runs with p50/p95 and fallback counts", () => {
    const summary = summarizeTaskTelemetry([
      run({ latencyMs: 100, usedTokens: 1000, relationshipCount: 1 }),
      run({ latencyMs: 200, usedTokens: 2000, relationshipCount: 2 }),
      run({ latencyMs: 1000, usedTokens: 3000, relationshipCount: 0, fallbackReason: "jev_disabled" }),
    ]);
    expect(summary.runs).toBe(3);
    expect(summary.meanTokens).toBe(2000);
    expect(summary.p50Latency).toBe(200);
    expect(summary.p95Latency).toBe(1000);
    expect(summary.relationships).toBe(3);
    expect(summary.fallbacks).toBe(1);
  });

  it("ignores malformed storage", () => {
    window.localStorage.setItem("evidence-layer.task-context.telemetry.v1", "{bad json");
    expect(readTaskTelemetry()).toEqual([]);
  });
});
