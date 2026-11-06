import { describe, expect, it } from "vitest";
import { calculateRoi, JEV_USD_PER_MILLION_INPUT, MODELS } from "../lib/roi-calculator";

const base = {
  volumeTokens: 1_000_000,
  modelId: "claude-haiku-5-5",
  scenario: "measured" as const,
  evidenceLayer: true,
  scorer: "lexical" as const,
};

describe("Evidence Layer ROI calculator", () => {
  it("applies the measured Haiku reduction and the 70/30 input split", () => {
    const result = calculateRoi(base);
    expect(result.tokens.with).toBeCloseTo(768_400, 6);
    expect(result.tokens.saved).toBeCloseTo(231_600, 6);
    expect(result.tokens.reductionPct).toBeCloseTo(23.16, 6);
    // Haiku: 0.7 × $0.10/M + 0.3 × $0.50/M = $0.22/M
    expect(result.usd.llmWithout).toBeCloseTo(0.22, 9);
    expect(result.usd.llmWith).toBeCloseTo(0.169048, 9);
    expect(result.usd.saved).toBeCloseTo(0.050952, 9);
  });

  it("uses the requested scenario rate", () => {
    const conservative = calculateRoi({ ...base, scenario: "conservative" });
    expect(conservative.assumptions.reduction).toBe(0.15);
    expect(conservative.tokens.with).toBeCloseTo(850_000, 6);
  });

  it("returns no saving when Evidence Layer is disabled", () => {
    const result = calculateRoi({ ...base, evidenceLayer: false });
    expect(result.tokens.with).toBe(result.tokens.without);
    expect(result.usd.saved).toBe(0);
    expect(result.usd.jev).toBe(0);
    expect(result.usd.systemWith).toBeCloseTo(result.usd.llmWithout, 12);
  });

  it("shows a negative saving for Haiku with JEV, matching the benchmark", () => {
    const result = calculateRoi({ ...base, scorer: "jev" });
    expect(result.usd.jev).toBeCloseTo((2_000_000 / 1_000_000) * JEV_USD_PER_MILLION_INPUT, 12);
    expect(result.usd.systemWith).toBeGreaterThan(result.usd.llmWithout);
    expect(result.usd.saved).toBeLessThan(0);
  });

  it("does not charge JEV for the lexical path", () => {
    const result = calculateRoi({ ...base, scorer: "lexical" });
    expect(result.usd.jev).toBe(0);
    expect(result.usd.systemWith).toBeCloseTo(result.usd.llmWith, 12);
  });

  it("includes every roster model with a measured or family-proxy rate", () => {
    expect(Object.keys(MODELS)).toHaveLength(8);
    for (const model of Object.values(MODELS)) {
      expect(model.measuredReduction).toBeGreaterThan(0.2);
      expect(model.measuredReduction).toBeLessThan(0.25);
    }
  });

  it("rejects negative volume and unknown models", () => {
    expect(() => calculateRoi({ ...base, volumeTokens: -1 })).toThrow(RangeError);
    expect(() => calculateRoi({ ...base, modelId: "unknown" })).toThrow(RangeError);
  });
});
