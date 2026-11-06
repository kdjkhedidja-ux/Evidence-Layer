# Phase 6 Usage Instrumentation

Status: **PASS for offline/local accounting contract**. This module does not call providers, does not persist user content, and is not a production billing ledger.

## Implementation

`src/evidence_layer/usage_measurement.py` provides:

- `UsageEvent` with run/case/arm/model/provider/stage/attempt identity
- input, output, cache-read, and duration-specific cache-write token fields (`cache_write_5m_tokens`, `cache_write_1h_tokens`, `cache_write_30m_tokens`, `cache_write_12h_tokens`)
- calls, retries, outcome, and timestamps
- explicit `actual`, `estimated`, and `unknown` token/cost semantics
- immutable `PricingSnapshot` versions, pinned on each usage event by `pricing_version`
- `cost_for_event` to recompute cost from usage and pricing inputs
- `summarize_events` to aggregate only values that are fully known

`input_tokens` is the total input count. Cache-read and 5m, 1h, 30m, and 12h cache-write counts are disjoint subsets of it; they are subtracted from uncached input before costing, so no token category is billed twice. Distinct write lifetimes use distinct price fields. Unknown tokens, cache categories, prices, pricing snapshots, or pricing-version mismatches produce `null` cost and a missing-reason code. Recalculation requires the exact `pricing_version` referenced by the event; it never silently substitutes the current price. Actual cost is reported only when usage status is actual and every nonzero category has its price; estimated tokens produce estimated cost.

The pinned pricing snapshot also declares each cache-write billing category as `separately_metered`, `included_in_input`, `not_applicable`, or `unknown`. `separately_metered` requires its own usage count and price; `included_in_input` stays within ordinary input cost; `not_applicable` permits a null usage field only when the pricing contract proves no such billable category; `unknown` blocks cost calculation. Missing telemetry is never re-labelled `not_applicable`.

The event contract contains no question text, passage body, answer text, credentials, or secret URL. It is evaluation-local and provider-neutral. Existing JEV-specific records remain unchanged; this contract does not enable JEV.

## Verification

`tests/test_usage_measurement.py` covers:

- exact cost recomputation from uncached, cache-read, and provider-specific cache-write lifetimes plus output tokens
- cache categories are disjoint and cannot exceed total input, preventing double counting
- missing cache-write usage/prices keep cost unknown
- serialized usage can be replayed against its exact pricing version; mismatched versions are rejected
- estimated-token cost labeling
- unknown usage remaining `null`
- aggregate calls, retries, and cached tokens
- invalid cached-token bounds

The tests run in `/tmp/evidence-layer-phase6-venv` with `pydantic==2.14.3` and `pytest==9.2.0`. No project dependency declaration was changed.

## Remaining integration gate

This is the accounting contract, not end-to-end provider instrumentation. Offline lexical/Evidence Layer events use local estimated usage with unknown cost. The next implementation review must add authorized provider adapters that preserve raw usage fields, enforce provider invariants, attach events to answer-run manifests, preserve failure/retry denominators, and add replayable aggregate artifacts. The eight model rows and pricing snapshots remain candidates, not operational approvals.
