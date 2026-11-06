# Phase 6 Provider Adapter Integration Design

Status: **LOCAL DESIGN REVIEW + FAKE HARNESS PASS**. The synthetic parsers and fake orchestration are not connected to provider clients. No provider call has occurred and external evaluation is not authorized.

## Current boundary

- `scripts/run_phase6_offline_arms.py` is explicitly local/offline and currently sets `answer_model=null`, `jev=not_run`, `network=none`.
- `parse_anthropic_usage`, `parse_openai_responses_usage`, and `parse_deepseek_chat_usage` parse already-received dictionaries only.
- `UsageEvent` records one attempt's run/case/arm/model/stage/attempt identity and metered token fields. The offline wrapper `ProviderAttempt` separately records requested/returned model IDs, stable ordinal, safe error category, and parser status; raw provider content remains outside the event record.
- Provider data egress is not authorized; no client construction belongs in the offline runner.

## Proposed future invocation boundary

When external test execution is separately approved, use a dedicated answer-evaluation orchestrator, not the offline evidence-arm runner:

1. Validate a frozen run manifest, candidate row, exact pricing version, egress approval, and provider-specific data-retention settings before constructing a client.
2. Build a request from the paired case/evidence-arm manifest. Do not place gold labels, other arms' results, or unapproved data in the request.
3. Allocate `attempt_id`, attempt ordinal, `run_id`, `case_id`, `arm_id`, requested model ID, pricing version, and local start timestamp **before** issuing the request.
4. Issue exactly one network request per attempt. Do not bury retry behavior in the SDK or HTTP transport; configure retries off where possible and verify the effective setting.
5. Keep response content in memory only for answer-quality processing. Pass the terminal response's usage object to the provider parser. Do not log raw request/response bodies.
6. Capture requested model ID and provider-returned model/version separately. If returned identity is missing or unexpectedly differs, mark the attempt `failure`/identity mismatch and do not pool it under the requested model without explicit policy.
7. Emit one Usage record for every HTTP attempt, including failed, refused, incomplete, and parser-error attempts. Never omit failed attempts from latency/call/retry denominators.
8. Recalculate event cost only against its exact pinned `pricing_version` and its per-TTL billing applicability. Missing usage, unknown billing policy, incomplete cache TTL details, or pricing mismatch yields `cost_usd=null` and a stable reason code.

## Event and retry requirements before wiring

Extend the event/run-attempt schema before integrating SDK clients:

- `requested_model_id` and `returned_model_id` must be separate fields; current `model_id` alone is ambiguous for model aliases.
- Add `attempt_ordinal` and a safe `adapter_error_category`/`usage_parse_status`; do not record exception strings or response bodies.
- Keep one event per network attempt. `calls=1` for every attempted HTTP call; `retries=0` for the initial attempt and `retries=1` for each retry attempt, so summed retry count equals retry attempts without inflating the number of calls.
- Populate `started_at`/`finished_at` from a monotonic timer for duration and wall-clock timestamps for audit. If no response is received, usage/token/cost fields stay null and outcome is failure/unknown.
- For a partial stream, parse usage only from the provider's documented terminal response/event. If the stream ends before terminal usage arrives, record a failed/incomplete attempt with unknown usage; never estimate it as actual.
- If a response has usage but the provider parser detects inconsistent counters, preserve only safe status (`usage_parse_status=invariant_failure`), null the normalized categories/cost, and keep the raw payload out of logs.
- The pricing version and response model identity are run-level provenance; do not infer them from a display name or a live pricing lookup.

## Retry behavior

- Every retry is a new attempt ID and a new Usage event.
- Retry only errors explicitly classified as transient in an approved policy; no retry for validation, policy, refusal, or unknown errors by default.
- Retry limits/backoff are frozen in the run manifest before execution.
- If a request may have succeeded remotely but the response was lost, record the uncertain attempt and do not replay silently; a new retry can incur another billable call.
- Answer selection uses one declared policy (first valid response or explicit retry resolution) applied equally to every model/arm. All attempts remain in cost and failure accounting.

## Privacy and secret handling

- API credentials are injected only into an isolated evaluation process through a secret manager; never browser, MCP, manifest, event, trace, exception, shell output, or repository files.
- Usage events contain IDs, counts, status, timing, price-version references, and safe error categories only. No question, evidence, generated answer, prompt, cache content, or provider body is logged.
- Provider request/response IDs may be retained only if reviewed for data sensitivity and needed for support; they are identifiers, not a substitute for local attempt IDs.
- Restrict provider egress to the approved synthetic/non-sensitive test set until data-transfer review specifically authorizes Real-World data.

## Actual-cost acceptance gate

An attempt may report `actual` cost only if all are true:

- provider returned a completed/terminal usage object;
- returned model identity is known and matches the approved candidate mapping;
- input, output, cache-read, and each separately metered write-lifetime count are known and satisfy provider invariants;
- every nonzero billed category has a rate in the immutable pricing version referenced by that event;
- per-TTL applicability is explicit (`separately_metered`, evidence-backed `included_in_input`/`not_applicable`, or else cost remains unknown);
- calculation reproduces from serialized event plus pricing snapshot.

DeepSeek remains cost-unknown until its cache-write billing category is resolved from official documentation or approved provider evidence. Do not turn cache misses into write tokens or infer non-applicability.

## Review result and remaining gate

**DESIGN REVIEW PASS; FAKE HARNESS PASS; REAL PROVIDER INTEGRATION NOT IMPLEMENTED.** Invocation boundary, retry accounting, partial/error handling, and safe logging are specified and locally exercised with fake clients. Before SDK wiring, obtain explicit provider/data-egress authorization. Model Matrix and Pricing Registry remain unfrozen/unapproved; JEV remains out of scope and unauthorized.

The fake-client harness now exercises these semantics locally (`src/evidence_layer/provider_execution.py`, `tests/test_provider_execution.py`; see `docs/phase6-provider-client-fake-integration.md`). Real SDK integration remains unimplemented and unauthorized.
