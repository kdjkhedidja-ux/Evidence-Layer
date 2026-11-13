import {
  calculateRoi,
  DEFAULT_INPUT_SHARE,
  JEV_USD_PER_MILLION_INPUT,
  MODELS,
  type Scenario,
  type Scorer,
} from "./roi-calculator";

export const DEFAULT_REQUESTS_PER_MONTH = 2_000;
export const DEFAULT_VOLUME_TOKENS = 1_000_000;
export const DEFAULT_RETRIEVAL_CALLS = 1;
export const DEFAULT_RETRIEVAL_P50_MS = 120;
export const DEFAULT_RETRIEVAL_P95_MS = 300;
export const DEFAULT_TTFT_P50_MS = 600;
export const DEFAULT_TTFT_P95_MS = 1_200;
export const DEFAULT_OUTPUT_TOKENS_PER_SECOND = 60;
export const DEFAULT_NETWORK_OVERHEAD_MS = 80;
export const DEFAULT_JEV_LATENCY_MS = 150;
export const DEFAULT_SUCCESS_RATE = 0.99;

export type PerformanceInput = {
  requestsPerMonth: number;
  volumeTokens: number;
  modelId: string;
  scenario: Scenario;
  evidenceLayer: boolean;
  scorer: Scorer;
  jevIntensity?: number;
  retrievalCallsPerRequest?: number;
  retrievalP50Ms?: number;
  retrievalP95Ms?: number;
  ttftP50Ms?: number;
  ttftP95Ms?: number;
  outputTokensPerSecond?: number;
  networkOverheadMs?: number;
  jevLatencyMs?: number;
  successRate?: number;
  inputShare?: number;
};

export type PerformanceResult = {
  requests: { total: number; successful: number };
  tokens: {
    perRequestWithout: number;
    perRequestWith: number;
    monthlyWithout: number;
    monthlyWith: number;
    outputPerRequest: number;
  };
  apiCalls: {
    perRequestWithout: number;
    perRequestWith: number;
    monthlyWithout: number;
    monthlyWith: number;
  };
  cost: {
    monthlyWithout: number;
    monthlyWith: number;
    per1kWithout: number;
    per1kWith: number;
    perSuccessfulWithout: number;
    perSuccessfulWith: number;
  };
  latency: {
    generationMs: number;
    baselineP50Ms: number;
    baselineP95Ms: number;
    withP50Ms: number;
    withP95Ms: number;
  };
  assumptions: {
    modelId: string;
    inputShare: number;
    successRate: number;
    retrievalCallsPerRequest: number;
    jevPerMillion: number;
  };
};

function finiteNonNegative(value: number, name: string): number {
  if (!Number.isFinite(value) || value < 0) throw new RangeError(`${name} must be a non-negative number`);
  return value;
}

function finitePositive(value: number, name: string): number {
  if (!Number.isFinite(value) || value <= 0) throw new RangeError(`${name} must be a positive number`);
  return value;
}

export function calculatePerformance(input: PerformanceInput): PerformanceResult {
  if (!MODELS[input.modelId]) throw new RangeError(`unknown model ${input.modelId}`);
  const requests = finitePositive(input.requestsPerMonth, "requestsPerMonth");
  const volume = finiteNonNegative(input.volumeTokens, "volumeTokens");
  const retrievalCalls = finiteNonNegative(
    input.retrievalCallsPerRequest ?? DEFAULT_RETRIEVAL_CALLS,
    "retrievalCallsPerRequest",
  );
  const retrievalP50 = finiteNonNegative(input.retrievalP50Ms ?? DEFAULT_RETRIEVAL_P50_MS, "retrievalP50Ms");
  const retrievalP95 = finiteNonNegative(input.retrievalP95Ms ?? DEFAULT_RETRIEVAL_P95_MS, "retrievalP95Ms");
  const ttftP50 = finiteNonNegative(input.ttftP50Ms ?? DEFAULT_TTFT_P50_MS, "ttftP50Ms");
  const ttftP95 = finiteNonNegative(input.ttftP95Ms ?? DEFAULT_TTFT_P95_MS, "ttftP95Ms");
  const outputTokensPerSecond = finitePositive(
    input.outputTokensPerSecond ?? DEFAULT_OUTPUT_TOKENS_PER_SECOND,
    "outputTokensPerSecond",
  );
  const networkOverhead = finiteNonNegative(
    input.networkOverheadMs ?? DEFAULT_NETWORK_OVERHEAD_MS,
    "networkOverheadMs",
  );
  const jevLatency = finiteNonNegative(input.jevLatencyMs ?? DEFAULT_JEV_LATENCY_MS, "jevLatencyMs");
  const successRate = Math.min(1, Math.max(0, input.successRate ?? DEFAULT_SUCCESS_RATE));
  const inputShare = Math.min(1, Math.max(0, input.inputShare ?? DEFAULT_INPUT_SHARE));

  const roi = calculateRoi({
    volumeTokens: volume,
    modelId: input.modelId,
    scenario: input.scenario,
    evidenceLayer: input.evidenceLayer,
    scorer: input.scorer,
    jevIntensity: input.jevIntensity,
    inputShare,
  });

  const perRequestWithout = volume / requests;
  const perRequestWith = roi.tokens.with / requests;
  const outputPerRequest = perRequestWithout * (1 - inputShare);
  const successful = requests * successRate;
  const jevCalls = input.evidenceLayer && input.scorer === "jev" ? 1 : 0;
  const apiCallsWith = input.evidenceLayer ? 1 + retrievalCalls + jevCalls : 1;
  const generationMs = (outputPerRequest / outputTokensPerSecond) * 1_000;

  const baselineP50 = ttftP50 + generationMs + networkOverhead;
  const baselineP95 = ttftP95 + generationMs + networkOverhead;
  const withP50 = input.evidenceLayer
    ? retrievalP50 + ttftP50 + generationMs + networkOverhead + jevCalls * jevLatency
    : baselineP50;
  const withP95 = input.evidenceLayer
    ? retrievalP95 + ttftP95 + generationMs + networkOverhead + jevCalls * jevLatency
    : baselineP95;

  const per1kWithout = requests === 0 ? 0 : (roi.usd.llmWithout / requests) * 1_000;
  const per1kWith = requests === 0 ? 0 : (roi.usd.systemWith / requests) * 1_000;

  return {
    requests: { total: requests, successful },
    tokens: {
      perRequestWithout,
      perRequestWith,
      monthlyWithout: roi.tokens.without,
      monthlyWith: roi.tokens.with,
      outputPerRequest,
    },
    apiCalls: {
      perRequestWithout: 1,
      perRequestWith: apiCallsWith,
      monthlyWithout: requests,
      monthlyWith: requests * apiCallsWith,
    },
    cost: {
      monthlyWithout: roi.usd.llmWithout,
      monthlyWith: roi.usd.systemWith,
      per1kWithout,
      per1kWith,
      perSuccessfulWithout: successful === 0 ? 0 : roi.usd.llmWithout / successful,
      perSuccessfulWith: successful === 0 ? 0 : roi.usd.systemWith / successful,
    },
    latency: {
      generationMs,
      baselineP50Ms: baselineP50,
      baselineP95Ms: baselineP95,
      withP50Ms: withP50,
      withP95Ms: withP95,
    },
    assumptions: {
      modelId: input.modelId,
      inputShare,
      successRate,
      retrievalCallsPerRequest: retrievalCalls,
      jevPerMillion: JEV_USD_PER_MILLION_INPUT,
    },
  };
}
