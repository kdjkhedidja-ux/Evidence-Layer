"use client";

import { useMemo, useState } from "react";
import {
  calculateRoi,
  DEFAULT_JEV_INTENSITY,
  MEASURED_RANGE,
  MODELS,
  SCENARIOS,
  type Scenario,
  type Scorer,
} from "../../../lib/roi-calculator";

type Mode = "projected" | "historical";

const tokenFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const usdFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});

export default function RoiCalculator() {
  const [mode, setMode] = useState<Mode>("projected");
  const [modelId, setModelId] = useState("gpt-6-luna");
  const [volume, setVolume] = useState(1_000_000);
  const [scenario, setScenario] = useState<Scenario>("median");
  const [evidenceLayer, setEvidenceLayer] = useState(true);
  const [scorer, setScorer] = useState<Scorer>("lexical");
  const [jevIntensity, setJevIntensity] = useState(DEFAULT_JEV_INTENSITY);

  const result = useMemo(() => {
    try {
      return calculateRoi({
        volumeTokens: Number.isFinite(volume) ? Math.max(0, volume) : 0,
        modelId,
        scenario,
        evidenceLayer,
        scorer,
        jevIntensity,
      });
    } catch {
      return null;
    }
  }, [volume, modelId, scenario, evidenceLayer, scorer, jevIntensity]);

  const model = MODELS[modelId];
  const barBase = result ? Math.max(result.tokens.without, result.usd.llmWithout, 0.000001) : 1;
  const tokenBar = result ? (result.tokens.with / barBase) * 100 : 0;
  const usdBar = result ? (result.usd.systemWith / barBase) * 100 : 0;

  return (
    <section className="public-section roi-calculator" aria-labelledby="roi-heading">
      <div className="benchmark-heading">
        <div>
          <p className="public-eyebrow">Estimate · projection</p>
          <h2 id="roi-heading">Token and cost calculator</h2>
        </div>
        <span className="benchmark-tag">Not an invoice</span>
      </div>
      <p>
        Applies the token reduction measured on Real-World v1 (40 cases) to your own volume, then
        estimates cost from published list prices. It does not promise an answer-quality gain.
      </p>

      <div className="roi-grid">
        <label className="roi-field">
          <span>Mode</span>
          <select value={mode} onChange={(event) => setMode(event.target.value as Mode)}>
            <option value="projected">Projected volume</option>
            <option value="historical">Past consumption</option>
          </select>
        </label>

        <label className="roi-field">
          <span>Answer model</span>
          <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
            {Object.values(MODELS).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>

        <label className="roi-field">
          <span>{mode === "projected" ? "Tokens per month without Evidence Layer" : "Tokens already consumed without Evidence Layer"}</span>
          <input
            type="number"
            min={0}
            step={1000}
            value={Number.isFinite(volume) ? volume : 0}
            onChange={(event) => setVolume(Number(event.target.value))}
          />
        </label>

        <label className="roi-field">
          <span>Reduction scenario</span>
          <select value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}>
            <option value="measured">Measured on this model</option>
            {Object.entries(SCENARIOS).map(([value, item]) => (
              <option key={value} value={value}>{item.label}</option>
            ))}
          </select>
        </label>

        <label className="roi-check">
          <input type="checkbox" checked={evidenceLayer} onChange={(event) => setEvidenceLayer(event.target.checked)} />
          <span>Use Evidence Layer selection</span>
        </label>

        <label className="roi-field">
          <span>Evidence scorer</span>
          <select
            value={scorer}
            disabled={!evidenceLayer}
            onChange={(event) => setScorer(event.target.value as Scorer)}
          >
            <option value="lexical">Lexical (no JEV)</option>
            <option value="jev">JEV (estimated)</option>
          </select>
        </label>

        {evidenceLayer && scorer === "jev" ? (
          <label className="roi-field">
            <span>JEV input tokens per baseline token (assumption)</span>
            <input
              type="number"
              min={0}
              max={5}
              step={0.1}
              value={jevIntensity}
              onChange={(event) => setJevIntensity(Number(event.target.value))}
            />
          </label>
        ) : null}
      </div>

      {result ? (
        <>
          <div className="roi-results" role="status">
            <div className="roi-result">
              <span>Tokens without EL</span>
              <strong>{tokenFormat.format(result.tokens.without)}</strong>
            </div>
            <div className="roi-result">
              <span>Tokens with EL</span>
              <strong>{tokenFormat.format(result.tokens.with)}</strong>
            </div>
            <div className="roi-result">
              <span>Tokens avoided</span>
              <strong className={result.tokens.saved >= 0 ? "ok" : "bad"}>
                {tokenFormat.format(result.tokens.saved)} ({result.tokens.reductionPct.toFixed(1)}%)
              </strong>
            </div>
            <div className="roi-result">
              <span>LLM cost without EL</span>
              <strong>{usdFormat.format(result.usd.llmWithout)}</strong>
            </div>
            <div className="roi-result">
              <span>LLM cost with EL</span>
              <strong>{usdFormat.format(result.usd.llmWith)}</strong>
            </div>
            <div className="roi-result">
              <span>Estimated JEV</span>
              <strong>{usdFormat.format(result.usd.jev)}</strong>
            </div>
            <div className="roi-result">
              <span>Estimated system cost with EL</span>
              <strong>{usdFormat.format(result.usd.systemWith)}</strong>
            </div>
            <div className="roi-result">
              <span>Estimated saving</span>
              <strong className={result.usd.saved >= 0 ? "ok" : "bad"}>{usdFormat.format(result.usd.saved)}</strong>
            </div>
          </div>

          <div className="roi-chart" aria-hidden="true">
            <div className="roi-bar-row">
              <span>Tokens</span>
              <div className="roi-track"><div className="roi-bar" style={{ width: `${Math.min(100, tokenBar)}%` }} /></div>
              <span>{result.tokens.reductionPct.toFixed(1)}% kept</span>
            </div>
            <div className="roi-bar-row">
              <span>System $</span>
              <div className="roi-track"><div className="roi-bar roi-bar--cost" style={{ width: `${Math.min(100, usdBar)}%` }} /></div>
              <span>{usdFormat.format(result.usd.systemWith)}</span>
            </div>
          </div>

          <p className="public-caveat">
            Assumptions: reduction {(result.assumptions.reduction * 100).toFixed(1)}% (
            {result.assumptions.reductionSource === "measured" ? "measured on this model" : "scenario"}
            {result.assumptions.rateSource === "family_proxy" ? ", family proxy" : ""});
            JEV at ${result.assumptions.jevPerMillion.toFixed(3)}/M input tokens ×{" "}
            {result.assumptions.jevIntensity.toFixed(1)} per baseline token;{" "}
            {(result.assumptions.inputShare * 100).toFixed(0)}% input split; prices editable in the
            module, list prices at the time of writing. Measured reduction range across the paired
            runs is {(MEASURED_RANGE.min * 100).toFixed(1)}%–{(MEASURED_RANGE.max * 100).toFixed(1)}%.
          </p>
        </>
      ) : (
        <p className="public-caveat" role="alert">Enter a non-negative token volume to calculate.</p>
      )}

      <p className="public-caveat">
        Estimates, not invoices. Token counts are a projection from measured rates and will differ
        with your prompt distribution. Quality is not guaranteed by these numbers.
      </p>
      <p className="public-report-links">
        <a href="/evaluation/phase7-report.html">See the measurements</a>
        <a href="/evaluation/haiku-lexical-report.html">Haiku without JEV</a>
      </p>
    </section>
  );
}
