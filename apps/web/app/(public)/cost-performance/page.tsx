import type { Metadata } from "next";
import { PublicSiteShell, ResponsiveTable } from "../../public-site-shell";
import RoiCalculator from "./roi-calculator";

export const metadata: Metadata = {
  title: "Cost & Performance | Evidence Layer",
  description: "Planning estimates for token usage, API calls, model cost, and end-to-end latency with and without Evidence Layer.",
};

export default function CostPerformancePage() {
  return (
    <PublicSiteShell
      currentPath="/cost-performance"
      eyebrow="Planning · operating metrics"
      title="Cost & Performance"
      description="Estimate the operating envelope of an Evidence Layer request: tokens, API calls, provider cost, and p50/p95 latency. Values are planning estimates, not an invoice or live account telemetry."
    >
      <RoiCalculator />

      <section className="public-section" aria-labelledby="metrics-title">
        <h2 id="metrics-title">Metrics worth tracking in production</h2>
        <p>
          These are the measures used by production LLM and retrieval platforms to separate useful
          work from overhead. Pair every cost or latency metric with a quality guardrail; a cheaper
          request is not a better request if coverage or correctness falls.
        </p>
        <ResponsiveTable label="Recommended production metrics for Evidence Layer workloads">
          <table className="public-table public-table--compact">
            <caption>Recommended metrics, definitions, and interpretation</caption>
            <thead>
              <tr><th scope="col">Metric</th><th scope="col">Definition / formula</th><th scope="col">Why it matters</th></tr>
            </thead>
            <tbody>
              <tr><th scope="row">Tokens per request</th><td>Input tokens + output tokens, averaged per request</td><td>Primary driver of model cost and context pressure.</td></tr>
              <tr><th scope="row">Input / output split</th><td>Input tokens ÷ total tokens</td><td>Selection changes input cost; generation changes output cost. Do not pool them.</td></tr>
              <tr><th scope="row">Tokens per successful request</th><td>Total tokens ÷ successful requests</td><td>Captures retries and failed requests that still consume tokens.</td></tr>
              <tr><th scope="row">API calls per request</th><td>Model calls + retrieval calls + scoring calls</td><td>Shows gateway, rate-limit, and per-call overhead introduced by selection.</td></tr>
              <tr><th scope="row">Cost per 1K requests</th><td>Monthly cost ÷ requests × 1,000</td><td>Volume-normalized unit economics for comparison and budgeting.</td></tr>
              <tr><th scope="row">Cost per successful request</th><td>Monthly cost ÷ successful requests</td><td>Penalizes failed attempts and retries; a better commercial unit than raw token price.</td></tr>
              <tr><th scope="row">Retrieval p50 / p95</th><td>Latency of the selection call at the 50th / 95th percentile</td><td>Separates retrieval overhead from model generation.</td></tr>
              <tr><th scope="row">TTFT p50 / p95</th><td>Time to first generated token</td><td>Dominates perceived responsiveness for streaming answers.</td></tr>
              <tr><th scope="row">End-to-end p50 / p95</th><td>Retrieval + TTFT + generation + network</td><td>The user-visible latency; averages can hide tail regressions.</td></tr>
              <tr><th scope="row">Output tokens / second</th><td>Generated tokens ÷ generation time</td><td>Explains generation latency and provider throughput differences.</td></tr>
              <tr><th scope="row">Error and retry rate</th><td>Failed calls ÷ total calls; retries ÷ requests</td><td>Retries add calls, tokens, cost, and tail latency.</td></tr>
              <tr><th scope="row">Cache hit rate</th><td>Cache hits ÷ eligible requests</td><td>Can reduce both cost and latency without changing the model.</td></tr>
              <tr><th scope="row">Context utilization</th><td>Selected context tokens ÷ model context window</td><td>High utilization risks truncation; low utilization may mean under-retrieval.</td></tr>
              <tr><th scope="row">Quality guardrail</th><td>Evidence coverage, correctness, or groundedness on a fixed set</td><td>Prevents cost and latency gains from hiding a quality regression.</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
      </section>

      <section className="public-section" aria-labelledby="method-title">
        <h2 id="method-title">How to use the estimate</h2>
        <ol>
          <li>Replace the example request volume and token counts with measured values from your workload.</li>
          <li>Use provider usage or invoices for prices, and measured p50/p95 values for retrieval and model latency.</li>
          <li>Compare the same fixed evaluation questions with and without Evidence Layer; do not compare unrelated runs.</li>
          <li>Review cost and latency together with a quality guardrail before changing a production route.</li>
        </ol>
        <p className="public-caveat">
          The planner uses published list prices and the measured benchmark reduction in the selected
          scenario. It does not include storage, egress, observability, retries beyond the success-rate
          input, taxes, or negotiated discounts. It is not a service-level commitment.
        </p>
      </section>
    </PublicSiteShell>
  );
}
