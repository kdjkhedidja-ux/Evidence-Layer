import { describe, expect, it } from "vitest";
import { calculatePerformance } from "../lib/performance-calculator";

const base = {
  requestsPerMonth: 1_000,
  volumeTokens: 1_000_000,
  modelId: "claude-haiku-5-5",
  scenario: "measured" as const,
  evidenceLayer: true,
  scorer: "lexical" as const,
};

describe("Evidence Layer performance planner", () => {
  it("derives per-request tokens, API calls, unit cost, and latency", () => {
    const result = calculatePerformance(base);
    expect(result.requests.successful).toBeCloseTo(990, 9);
    expect(result.tokens.perRequestWithout).toBeCloseTo(1_000, 9);
    expect(result.tokens.perRequestWith).toBeCloseTo(195, 9);
    expect(result.tokens.outputPerRequest).toBeCloseTo(300, 9);
    expect(result.apiCalls.perRequestWithout).toBe(1);
    expect(result.apiCalls.perRequestWith).toBe(2);
    expect(result.apiCalls.monthlyWith).toBe(2_000);
    expect(result.cost.per1kWithout).toBeCloseTo(0.22, 9);
    expect(result.cost.per1kWith).toBeCloseTo(0.0429, 9);
    expect(result.cost.perSuccessfulWith).toBeCloseTo(0.0429 / 990, 12);
    expect(result.latency.generationMs).toBeCloseTo(5_000, 9);
    expect(result.latency.baselineP50Ms).toBeCloseTo(5_680, 9);
    expect(result.latency.withP50Ms).toBeCloseTo(5_800, 9);
    expect(result.latency.baselineP95Ms).toBeCloseTo(6_280, 9);
    expect(result.latency.withP95Ms).toBeCloseTo(6_580, 9);
  });

  it("adds one API call and the configured latency for JEV", () => {
    const result = calculatePerformance({ ...base, scorer: "jev", jevLatencyMs: 200 });
    expect(result.apiCalls.perRequestWith).toBe(3);
    expect(result.apiCalls.monthlyWith).toBe(3_000);
    expect(result.latency.withP50Ms).toBeCloseTo(baseLatencyP50() + 200, 9);
  });

  it("does not add retrieval or JEV overhead when Evidence Layer is disabled", () => {
    const result = calculatePerformance({ ...base, evidenceLayer: false });
    expect(result.apiCalls.perRequestWith).toBe(1);
    expect(result.apiCalls.monthlyWith).toBe(1_000);
    expect(result.latency.withP50Ms).toBeCloseTo(result.latency.baselineP50Ms, 9);
    expect(result.cost.monthlyWith).toBeCloseTo(result.cost.monthlyWithout, 9);
  });

  it("uses the configured success rate for the successful-request denominator", () => {
    const result = calculatePerformance({ ...base, successRate: 0.5 });
    expect(result.requests.successful).toBeCloseTo(500, 9);
    expect(result.cost.perSuccessfulWith).toBeCloseTo(result.cost.monthlyWith / 500, 9);
  });

  it("rejects impossible request, throughput, and model inputs", () => {
    expect(() => calculatePerformance({ ...base, requestsPerMonth: 0 })).toThrow(RangeError);
    expect(() => calculatePerformance({ ...base, outputTokensPerSecond: 0 })).toThrow(RangeError);
    expect(() => calculatePerformance({ ...base, modelId: "unknown" })).toThrow(RangeError);
  });
});

function baseLatencyP50() {
  return 120 + 600 + 5_000 + 80;
}
