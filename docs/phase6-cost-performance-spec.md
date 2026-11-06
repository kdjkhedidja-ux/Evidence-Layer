# Phase 6 Cost and Performance Specification

The offline event/accounting contract is implemented in
`src/evidence_layer/usage_measurement.py` and documented in
`docs/phase6-usage-instrumentation.md`. This does not enable provider calls or
constitute a billing ledger.

## Scope

Cost & Performance reports measured usage and forecasts; it is not an invoice, quota authority, or billing ledger. It must distinguish `actual`, `estimated`, and `forecast` values and preserve unknowns as null.

## Usage event

Each attempt emits a privacy-safe event with:

```json
{
  "run_id": "…",
  "case_id": "…",
  "arm_id": "…",
  "model_id": "…",
  "provider_id": "…",
  "stage": "retrieval|jev|selection|answer|retry",
  "attempt_id": "…",
  "started_at": "…",
  "finished_at": "…",
  "input_tokens": null,
  "provider_output_tokens": null,
  "reasoning_tokens": null,
  "visible_output_tokens": null,
  "cache_read_input_tokens": null,
  "cache_write_5m_tokens": null,
  "cache_write_1h_tokens": null,
  "cache_write_30m_tokens": null,
  "cache_write_12h_tokens": null,
  "calls": 0,
  "retries": 0,
  "outcome": "success|failure|refusal|unknown",
  "cost_usd": null,
  "cost_status": "actual|estimated|forecast|unknown",
  "missing_cost_reason": null,
  "pricing_version": null
}
```

No event contains query text, passage bodies, answer text, credentials, URLs with secrets, or user content. IDs are evaluation-local and aggregate reports use hashes or counts.

## Cost model

```text
C_total = C_retrieval + C_JEV + C_selection + C_LLM
          + C_runtime + C_retries
CostPerSuccessfulTask = total measured cost / successful grounded tasks
```

Provider-reported usage is the authority for actual input/output/cached token cost. Local tokenizer counts are estimates and must identify tokenizer/rendering versions. Unknown components remain null; a complete net-savings claim is invalid when a required component is unknown.

`input_tokens` is the total input. Cache-read and 5m/1h/30m/12h cache-write lifetime categories are disjoint subsets of that total and must not be added on top of it. Cost calculation subtracts these categories from uncached input, then applies each category's own rate. If a provider reports writes without separating lifetime categories required by the price schedule, cost remains unknown.

`provider_output_tokens` is the provider's billable total output count and is used exactly once for cost. `reasoning_tokens` and `visible_output_tokens` are descriptive output subsets/measurements, not additional billed token categories. Never add them to `provider_output_tokens` during cost calculation. Preserve provider-reported reasoning counts when available; visible-answer token counts require a pinned local tokenizer/version applied only to visible text. Missing counts remain null and do not invalidate provider-reported total output cost unless that provider's billing requires the category.

Each pricing version must set cache-write billing applicability per lifetime: `separately_metered`, `included_in_input`, `not_applicable`, or `unknown`. The first requires its own usage count and rate; the second is charged only at the ordinary input rate; the third permits an absent counter only when the provider's pricing contract proves that category does not apply; `unknown` prevents actual cost. Missing telemetry is never converted to `not_applicable`.

## Live view and forecast

The future Evidence Explorer view may show per-stage tokens, calls, retries, latency, actual cost, and forecast cost. It must label values by status and show the pricing version/date. Forecasts use stratified distributions by question type, arm, model, evidence size, retry rate, and provider usage; output includes an interval and sample size. Forecasting 1,000/10,000/100,000 tasks must not collapse uncertainty into a single exact number.

## Performance

Record stage and end-to-end latency, p50/p95/p99 where sample size supports it, timeout/error rates, retry counts, and correct-refusal rate. A lower latency or token count is not a success if grounded answer quality or required-fact coverage regresses.

## Completeness gates

- All required stages emit events or an explicit unavailable reason.
- Retries and failed calls remain in the denominator.
- Actual and forecast values cannot be mixed in one aggregate without labels.
- Pricing snapshots are immutable; corrections create a new version.
- Replays are deterministic where the model policy promises determinism and are otherwise tagged nondeterministic.
