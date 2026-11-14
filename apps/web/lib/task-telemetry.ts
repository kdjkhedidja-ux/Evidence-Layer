export type TaskContextTelemetry = {
  recordedAt: string;
  status: string;
  latencyMs: number;
  usedTokens: number;
  usedChars: number;
  selectedCount: number;
  chunkCount: number;
  matchedChunkCount: number;
  relationshipCount: number;
  scoringProvider: string;
  fallbackReason: string | null;
  topK: number;
  maxTokens: number;
  maxChars: number;
  useJev: boolean;
};

export type TaskTelemetrySummary = {
  runs: number;
  meanTokens: number;
  p50Latency: number;
  p95Latency: number;
  relationships: number;
  fallbacks: number;
};

const STORAGE_KEY = "evidence-layer.task-context.telemetry.v1";
const MAX_RECORDS = 100;

export function readTaskTelemetry(storage: Pick<Storage, "getItem"> | null = browserStorage()): TaskContextTelemetry[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isTaskContextTelemetry).slice(0, MAX_RECORDS);
  } catch {
    return [];
  }
}

export function recordTaskTelemetry(
  run: TaskContextTelemetry,
  storage: Pick<Storage, "getItem" | "setItem"> | null = browserStorage(),
): TaskContextTelemetry[] {
  if (!storage) return [];
  const next = [run, ...readTaskTelemetry(storage)].slice(0, MAX_RECORDS);
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    return readTaskTelemetry(storage);
  }
  return next;
}

export function clearTaskTelemetry(storage: Pick<Storage, "removeItem"> | null = browserStorage()): void {
  try {
    storage?.removeItem(STORAGE_KEY);
  } catch {
    // A blocked storage API must not break the page.
  }
}

export function summarizeTaskTelemetry(runs: TaskContextTelemetry[]): TaskTelemetrySummary {
  if (runs.length === 0) {
    return { runs: 0, meanTokens: 0, p50Latency: 0, p95Latency: 0, relationships: 0, fallbacks: 0 };
  }
  const latency = runs.map((run) => run.latencyMs).sort((a, b) => a - b);
  return {
    runs: runs.length,
    meanTokens: runs.reduce((sum, run) => sum + run.usedTokens, 0) / runs.length,
    p50Latency: percentile(latency, 50),
    p95Latency: percentile(latency, 95),
    relationships: runs.reduce((sum, run) => sum + run.relationshipCount, 0),
    fallbacks: runs.filter((run) => Boolean(run.fallbackReason)).length,
  };
}

function percentile(values: number[], percent: number): number {
  if (values.length === 0) return 0;
  const index = Math.min(values.length - 1, Math.max(0, Math.ceil((percent / 100) * values.length) - 1));
  return values[index];
}

function browserStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function isTaskContextTelemetry(value: unknown): value is TaskContextTelemetry {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.recordedAt === "string" &&
    typeof record.status === "string" &&
    typeof record.latencyMs === "number" &&
    typeof record.usedTokens === "number" &&
    typeof record.usedChars === "number" &&
    typeof record.selectedCount === "number" &&
    typeof record.chunkCount === "number" &&
    typeof record.matchedChunkCount === "number" &&
    typeof record.relationshipCount === "number" &&
    typeof record.scoringProvider === "string" &&
    (record.fallbackReason === null || typeof record.fallbackReason === "string") &&
    typeof record.topK === "number" &&
    typeof record.maxTokens === "number" &&
    typeof record.maxChars === "number" &&
    typeof record.useJev === "boolean"
  );
}
