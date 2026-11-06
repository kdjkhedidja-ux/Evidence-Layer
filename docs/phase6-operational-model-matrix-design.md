# Phase 6 Operational Model Matrix Design

Status: **DESIGN REVIEW; NOT FROZEN; NO API CALLS AUTHORIZED**. Candidate Roster v1 is fixed at eight entries. This document translates the official public API documentation into a proposed request profile and lists decisions/validation still required. No SDK request has been sent.

## Frozen candidate roster

| Provider | Requested model IDs | Candidate count |
|---|---|---:|
| Anthropic | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` | 3 |
| OpenAI | `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna` | 3 |
| DeepSeek | `deepseek-flash`, `deepseek-v4-pro` | 2 |
| Excluded | `gpt-6-astra` | 0 |

The roster is frozen as a candidate list only. It does not approve vendor egress, operational settings, pricing, or the returned ID.

## Proposed common request contract

- One stateless answer request for each `(run_id, case_id, evidence_arm, candidate_model)` cell.
- Identical versioned prompt template, evidence bytes/order, output schema, refusal rubric, and **visible answer target <=800 tokens** across paired evidence arms. Measure visible answer separately from reasoning.
- No server tools, web search, retrieval, functions, code execution, or MCP.
- One provider call per attempt; SDK/transport retries disabled or surfaced as separate attempts.
- Hash the rendered prompt and output artifact; do not persist either in Usage logs.
- Requested model ID and provider-returned model ID/version are separate fields. Missing returned ID stays null; mismatch blocks pooling and is never replaced with the requested ID.
- The visible answer target is **<=800 tokens**, as decided. This is not the provider's hard total output cap. A hard API cap must be selected per candidate, with enough space for reasoning where enabled. Do not truncate after generation; count visible answer text with a pinned tokenizer and mark violations. A common API hard cap is neither selected nor implied.

## Per-provider API contract

| Provider | Endpoint/API version | Candidate IDs and pinned version behavior | Proposed generation settings | Cache policy | Usage mapping required |
|---|---|---|---|---|---|
| Anthropic | Messages `POST /v1/messages`; header `anthropic-version: 2026-10-21` | Three dateless IDs are documented as pinned model snapshots; response `model` must still be stored exactly. | Visible target <=800; total `max_tokens` **PENDING per candidate**; `output_config.effort=medium`; adaptive thinking remains on. API version removes temperature, top_p, and top_k. | No `cache_control` block; prompt caching is opt-in. Unexpected cache usage must be surfaced, not discarded. | Total input equation and cache TTL validation as below; raw `usage.output_tokens` maps to `provider_output_tokens`. No separate reasoning counter is documented, so `reasoning_tokens=null` (never zero). Visible text is measured with a pinned tokenizer. |
| OpenAI | Responses `POST /v1/responses`; API route is `/v1`; record exact SDK semver because no date API version is selected here. | Each candidate's listed ID is currently its documented default snapshot. Capture response `model` separately; do not assume an alias is unchanged. | Visible target <=800; `max_output_tokens` **PENDING per candidate**; `reasoning.effort=low`; `store=false`; omit temperature. | `prompt_cache_options.mode=explicit` and no breakpoints. Official docs say no cache use/write; counters must be zero or the profile is invalid. | Input total, cached input, aggregate write + 30m/12h TTL validation; raw `usage.output_tokens` maps to `provider_output_tokens`; capture `output_tokens_details.reasoning_tokens` as a subset; visible text measured separately. |
| DeepSeek | Chat Completions `POST /chat/completions` using documented base URL `https://api.deepseek.com`; no date API header, so pin the returned model version and capture SDK semver. This aligns with the synthetic Chat Completions parser. | `deepseek-flash` maps to `DeepSeek-V4.1-Flash-1017`; `deepseek-v4-pro` maps to `DeepSeek-V4-Pro-0813`; retain response `model` exactly. | Visible target <=800; total `max_tokens` **PENDING**; `thinking.type=disabled`; `temperature=0`; `top_p=1`. This disables requested hidden reasoning but does not imply cross-provider equivalence. | Context caching is automatic and no disable control is documented. Log hit/miss and request order. Cache-write applicability remains unknown. | Validate prompt=hit+miss; raw `usage.completion_tokens` maps to `provider_output_tokens`; capture `completion_tokens_details.reasoning_tokens` if present; visible `message.content` measured separately, excluding `reasoning_content`. Cache-write counters remain null; never map misses to writes. |

### Model-level limits

| Provider candidates | Context window | Maximum input | Maximum output |
|---|---:|---:|---:|
| Anthropic Opus/Sonnet/Haiku 5.5 | 1M | Request limit subject to output reservation | 128K |
| OpenAI GPT-6.1 Sol/Terra and GPT-6 Luna | 1.05M | 922K | 128K |
| DeepSeek Flash and V4 Pro | 1M | Remaining context after output reservation | 384K |

These are provider-published ceilings, not benchmark settings. The benchmark Evidence Layer budget and prompt are much smaller; reject over-limit requests rather than silently truncate.

## Per-candidate acceptance matrix

`DOC` means verified in official public docs; `PROPOSED` is a test-profile value not yet approved; `PENDING` blocks matrix freeze until the named decision or authorized validation is complete.

| Candidate | Requested model / API profile | Reasoning and sampling | Visible / hard output | Cache profile | Returned identity and usage fields | Acceptance status |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5`; Messages, API `2026-10-21`; pinned snapshot ID | Adaptive thinking always on; effort medium proposed; no temperature/top_p/top_k | visible <=800; hard `max_tokens` pending; includes reasoning | no cache-control, opt-in cache omitted | response `model` exact; input/read/create TTL; output total; reasoning count unavailable in usage | DOC limits/settings; output and SDK validation PENDING |
| Claude Sonnet 5.5 | `claude-sonnet-5-5`; Messages, API `2026-10-21`; pinned snapshot ID | Adaptive thinking; medium proposed override from high default; no sampling params in API version | visible <=800; hard `max_tokens` pending; includes reasoning | no cache-control | same Anthropic usage; output total includes reasoning | DOC limits/settings; output and SDK validation PENDING |
| Claude Haiku 5.5 | `claude-haiku-5-5`; Messages, API `2026-10-21`; pinned snapshot ID | Adaptive thinking; medium default/proposed; no sampling params in API version | visible <=800; hard `max_tokens` pending; includes reasoning | no cache-control | same Anthropic usage; pricing prompt tier >100K must be captured | DOC limits/settings; output and SDK validation PENDING |
| GPT-6.1 Sol | `gpt-6.1-sol`; Responses `/v1/responses`; listed snapshot ID | `reasoning.effort=low` proposed; temperature omitted | visible <=800; hard `max_output_tokens` pending; includes reasoning | explicit-only/no breakpoints; cached/write counters expected 0 | response `model`; input/cache/write TTL/reasoning tokens | DOC endpoint and counters; hard cap/SDK validation PENDING |
| GPT-6.1 Terra | `gpt-6.1-terra`; Responses `/v1/responses`; listed snapshot ID | `reasoning.effort=low` proposed; temperature omitted | visible <=800; hard `max_output_tokens` pending; includes reasoning | explicit-only/no breakpoints | same OpenAI usage; returned ID exact | DOC endpoint and counters; hard cap/SDK validation PENDING |
| GPT-6 Luna | `gpt-6-luna`; Responses `/v1/responses`; listed snapshot ID | `reasoning.effort=low` proposed; temperature omitted | visible <=800; hard `max_output_tokens` pending; includes reasoning | explicit-only/no breakpoints | same OpenAI usage; returned ID exact | DOC endpoint and counters; hard cap/SDK validation PENDING |
| DeepSeek Flash | `deepseek-flash`; Chat Completions; maps to `DeepSeek-V4.1-Flash-1017` | thinking disabled; temperature 0, top_p 1 proposed | visible <=800; hard `max_tokens` pending | auto context caching; cannot disable | response model; prompt hit/miss; reasoning_tokens if supplied; writes null/unknown policy | DOC fields; total cap, SDK and cache billing PENDING |
| DeepSeek V4 Pro | `deepseek-v4-pro`; Chat Completions; maps to `DeepSeek-V4-Pro-0813` | thinking disabled; temperature 0, top_p 1 proposed | visible <=800; hard `max_tokens` pending | auto context caching; cannot disable | same DeepSeek usage; cache-write applicability unknown | DOC fields; total cap, SDK and cache billing PENDING |

**Visible-answer enforcement:** the prompt requests <=800 visible tokens; post-response validation uses a pinned tokenizer/version per model. Over-limit answers are marked as violations and are not truncated. Tokenizer availability/mapping and the safe hard total output caps remain pending; therefore this is not yet executable.

## Returned-model and API-version rules

- Anthropic's `anthropic-version` is pinned to `2026-10-21` in the proposal. Store the returned Message `model` verbatim.
- OpenAI currently has no chosen date-style per-request API version in this design. Pin the Responses route, SDK package version, documentation retrieval date, request shape hash, requested model ID, and returned `model`.
- DeepSeek has no date-style API version in this design. Pin Chat Completions route, SDK package version, documented model build, requested ID, and returned `model`.
- If the response omits `model`, preserve `returned_model_id=null`; the attempt is not eligible for pooled model results. If returned ID differs from the candidate mapping, record mismatch and do not relabel it.

## Required usage and cost fields

Every attempt records `input_tokens`, `provider_output_tokens`, `reasoning_tokens`, `visible_output_tokens` with its tokenizer ID, `output_token_measurement_status`, cache read and all duration-specific cache-write counters, `pricing_version`, returned/requested model IDs, request timing, calls, retry attempt, outcome, and safe parser status. Reasoning and visible counts are subsets of provider output and are never added to it for cost. The four billing applicability policies remain in the pricing snapshot. Unknown cache-write billing (DeepSeek) yields `cost=null`; no missing counter or applicability is changed to zero. Costs from candidate prices are `estimated` until an authorized provider response validates the usage mapping and the candidate price/version.

## Decisions and checks still pending

1. Select a per-candidate hard total-output cap that leaves room for hidden reasoning; the <=800 visible answer target is separate.
2. Approve provider-specific effort/sampling profiles; differences are reported and not described as equivalent. Reasoning-token/visible-answer instrumentation needs schema and tokenizer work.
3. Review cache asymmetry: Anthropic prompt caching omitted, OpenAI explicit-only/no-breakpoints, DeepSeek automatic cache. Decide if a separate cost/cache experiment is needed.
4. Resolve DeepSeek cache-write pricing applicability using authoritative provider documentation; until then its cost remains unknown.
5. Set provider data region, retention, egress, secret-manager, and reviewer approvals independently for each provider.
6. Obtain authorization for synthetic/non-sensitive SDK validation. Only then may live response model IDs, supported settings, usage objects, retries, and returned fields be verified.

## Current disposition

Candidate Roster v1 is frozen as a roster. This operational design is **NOT FROZEN**: output cap, effort fairness, cache profile, returned-model validation, SDK pins, pricing/effective dates, DeepSeek applicability, and egress approvals remain pending. No provider/JEV call is authorized.

## Official sources reviewed

- Anthropic [API versioning](https://platform.claude.com/docs/en/api/versioning), [Models overview](https://platform.claude.com/docs/en/models/overview), [Effort](https://platform.claude.com/docs/en/build-with-claude/effort).
- OpenAI [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
- DeepSeek [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing), [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode), [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/), [Context Caching](https://api-docs.deepseek.com/guides/kv_cache/).

Sources reviewed 2026-11-06. No API calls were made.
