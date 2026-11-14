/**
 * Evidence Layer benefit calculator.
 *
 * Token counts are a projection: measured reduction rate × user volume.
 * Dollar amounts are estimates from published list prices, never invoices.
 * No answer-quality gain is implied by these numbers.
 */

export type Scorer = "lexical" | "jev";
export type Scenario = "conservative" | "median" | "optimistic" | "measured";
export type RateSource = "measured" | "family_proxy";

export type ModelPricing = {
  id: string;
  label: string;
  family: "openai" | "deepseek" | "anthropic";
  /** Fraction of total tokens avoided by Evidence Layer in the paired run. */
  measuredReduction: number;
  rateSource: RateSource;
  inputPerMillion: number;
  outputPerMillion: number;
};

export const MODELS: Readonly<Record<string, ModelPricing>> = {
  "gpt-6.1-sol": { id: "gpt-6.1-sol", label: "OpenAI GPT-6.1 Sol · observed A/B", family: "openai", measuredReduction: 0.807, rateSource: "measured", inputPerMillion: 2.0, outputPerMillion: 10.0 },
  "gpt-6.1-terra": { id: "gpt-6.1-terra", label: "OpenAI GPT-6.1 Terra · provider proxy", family: "openai", measuredReduction: 0.807, rateSource: "family_proxy", inputPerMillion: 1.0, outputPerMillion: 5.0 },
  "gpt-6-luna": { id: "gpt-6-luna", label: "OpenAI GPT-6 Luna · provider proxy", family: "openai", measuredReduction: 0.807, rateSource: "family_proxy", inputPerMillion: 0.1, outputPerMillion: 0.5 },
  "deepseek-v4-pro": { id: "deepseek-v4-pro", label: "DeepSeek Chat · observed A/B", family: "deepseek", measuredReduction: 0.809, rateSource: "measured", inputPerMillion: 0.66, outputPerMillion: 1.98 },
  "deepseek-flash": { id: "deepseek-flash", label: "DeepSeek Flash · provider proxy", family: "deepseek", measuredReduction: 0.809, rateSource: "family_proxy", inputPerMillion: 0.15, outputPerMillion: 0.6 },
  "claude-haiku-5-5": { id: "claude-haiku-5-5", label: "Anthropic Claude Haiku 5.5 · observed A/B", family: "anthropic", measuredReduction: 0.805, rateSource: "measured", inputPerMillion: 0.1, outputPerMillion: 0.5 },
  "claude-sonnet-5-5": { id: "claude-sonnet-5-5", label: "Anthropic Claude Sonnet 5.5 · provider proxy", family: "anthropic", measuredReduction: 0.805, rateSource: "family_proxy", inputPerMillion: 2.0, outputPerMillion: 10.0 },
  "claude-opus-5-5": { id: "claude-opus-5-5", label: "Anthropic Claude Opus 5.5 · provider proxy", family: "anthropic", measuredReduction: 0.805, rateSource: "family_proxy", inputPerMillion: 4.0, outputPerMillion: 20.0 },
};

export const SCENARIOS: Readonly<Record<Exclude<Scenario, "measured">, { label: string; reduction: number }>> = {
  conservative: { label: "Conservative (65%)", reduction: 0.65 },
  median: { label: "Observed median (80.7%)", reduction: 0.807 },
  optimistic: { label: "Optimistic (85%)", reduction: 0.85 },
};

export const MEASURED_RANGE = { min: 0.805, max: 0.809 };
export const JEV_USD_PER_MILLION_INPUT = 0.042;
/** Phase 6 measured ~1,904.6 JEV input tokens per ~900-token case. */
export const DEFAULT_JEV_INTENSITY = 2.0;
export const DEFAULT_INPUT_SHARE = 0.7;

export type RoiInput = {
  volumeTokens: number;
  modelId: string;
  scenario: Scenario;
  evidenceLayer: boolean;
  scorer: Scorer;
  /** JEV input tokens per token of baseline volume; only used with scorer="jev". */
  jevIntensity?: number;
  inputShare?: number;
  inputPricePerMillion?: number;
  outputPricePerMillion?: number;
};

export type RoiResult = {
  tokens: { without: number; with: number; saved: number; reductionPct: number };
  usd: { llmWithout: number; llmWith: number; jev: number; systemWith: number; saved: number };
  assumptions: {
    reduction: number;
    reductionSource: "scenario" | "measured";
    modelId: string;
    rateSource: RateSource;
    scorer: Scorer;
    jevIntensity: number;
    inputShare: number;
    inputPerMillion: number;
    outputPerMillion: number;
    jevPerMillion: number;
  };
};

function finiteNonNegative(value: number, name: string): number {
  if (!Number.isFinite(value) || value < 0) throw new RangeError(`${name} must be a non-negative number`);
  return value;
}

export function reductionFor(input: Pick<RoiInput, "modelId" | "scenario">): { value: number; source: "scenario" | "measured" } {
  const model = MODELS[input.modelId];
  if (!model) throw new RangeError(`unknown model ${input.modelId}`);
  if (input.scenario === "measured") return { value: model.measuredReduction, source: "measured" };
  return { value: SCENARIOS[input.scenario].reduction, source: "scenario" };
}

export function calculateRoi(input: RoiInput): RoiResult {
  const model = MODELS[input.modelId];
  if (!model) throw new RangeError(`unknown model ${input.modelId}`);
  const volume = finiteNonNegative(input.volumeTokens, "volumeTokens");
  const reduction = reductionFor(input);
  const jevIntensity = finiteNonNegative(input.jevIntensity ?? DEFAULT_JEV_INTENSITY, "jevIntensity");
  const inputShare = Math.min(1, Math.max(0, input.inputShare ?? DEFAULT_INPUT_SHARE));
  const inputPrice = finiteNonNegative(input.inputPricePerMillion ?? model.inputPerMillion, "inputPricePerMillion");
  const outputPrice = finiteNonNegative(input.outputPricePerMillion ?? model.outputPerMillion, "outputPricePerMillion");

  const withTokens = input.evidenceLayer ? volume * (1 - reduction.value) : volume;
  const savedTokens = volume - withTokens;

  const llmCost = (totalTokens: number): number =>
    (totalTokens * inputShare * inputPrice) / 1_000_000 +
    (totalTokens * (1 - inputShare) * outputPrice) / 1_000_000;

  const llmWithout = llmCost(volume);
  const llmWith = llmCost(withTokens);
  const jevTokens = input.evidenceLayer && input.scorer === "jev" ? volume * jevIntensity : 0;
  const jev = (jevTokens / 1_000_000) * JEV_USD_PER_MILLION_INPUT;
  const systemWith = input.evidenceLayer ? llmWith + jev : llmWithout;
  const saved = llmWithout - systemWith;

  return {
    tokens: {
      without: volume,
      with: withTokens,
      saved: savedTokens,
      reductionPct: volume === 0 ? 0 : (savedTokens / volume) * 100,
    },
    usd: { llmWithout, llmWith, jev, systemWith, saved },
    assumptions: {
      reduction: reduction.value,
      reductionSource: reduction.source,
      modelId: model.id,
      rateSource: model.rateSource,
      scorer: input.scorer,
      jevIntensity,
      inputShare,
      inputPerMillion: inputPrice,
      outputPerMillion: outputPrice,
      jevPerMillion: JEV_USD_PER_MILLION_INPUT,
    },
  };
}
