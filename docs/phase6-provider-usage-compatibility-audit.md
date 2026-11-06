# Phase 6 Provider Usage Compatibility Audit

Audit date: 2026-11-06. This audit reviewed public official API documentation only. No provider API/model-list/generation calls were made and no benchmark data was sent externally.

## Contract under review

`src/evidence_layer/usage_measurement.py` expects:

- total `input_tokens` and `provider_output_tokens` (normalized from each API's raw output field)
- `reasoning_tokens` as a reported subset when the provider exposes it
- `visible_output_tokens` measured from visible response text using a separately pinned tokenizer
- cache-read input tokens
- cache-write tokens categorized by 5m, 1h, 30m, and 12h lifetimes
- exact `pricing_version`
- categories to be disjoint subsets of total input

The cost calculator uses a per-TTL billing policy: `separately_metered`, `included_in_input`, `not_applicable`, or `unknown`. The cost remains unknown when a required category is unknown or a separately metered counter/rate is absent. Synthetic local parsers now implement the documented mappings in `src/evidence_layer/provider_usage.py`; they are not wired to provider clients (`docs/phase6-provider-usage-adapters.md`).

The P6 metering contract now includes this applicability state in `PricingSnapshot`; it does not classify DeepSeek as `not_applicable` without evidence. The policy distinguishes a missing usage counter from a documented non-applicable billed category.

## Compatibility matrix

| Provider / proposed endpoint | Input/output usage | Cache read | Cache write | Mapping to local fields | Current cost usability |
|---|---|---|---|---|---|
| Anthropic Messages API | Raw `usage.output_tokens` maps to provider total; `usage.input_tokens` is uncached input. Thinking is included in provider output; no separate reasoning counter is documented. | `cache_read_input_tokens` | `cache_creation_input_tokens`, with `cache_creation.ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` TTL split | Normalize total input by summing uncached + cache reads + writes. Map nested TTL fields to 5m/1h; require aggregate creation equals TTL sum. `reasoning_tokens=null` means unreported, never zero. | Cost uses provider total output; visible answer count is local-tokenizer measured. |
| OpenAI Responses API | Raw `usage.output_tokens` maps to provider total; `output_tokens_details.reasoning_tokens` is a subset | `usage.input_tokens_details.cached_tokens` | `usage.input_tokens_details.cache_write_tokens`; TTL details `cache_write_tokens_details.ttl_30m_tokens`, `ttl_12h_tokens` | Input total; require TTL sum equals aggregate writes. Capture reasoning tokens as a subset only; do not add them to provider total for cost. | Compatible when response includes required write TTL details; visible text separately counted with pinned tokenizer. |
| DeepSeek Chat Completions | Raw `usage.completion_tokens` maps to provider total; `completion_tokens_details.reasoning_tokens` is a subset when present | `usage.prompt_cache_hit_tokens` (`prompt_tokens_details.cached_tokens` is also documented as equivalent) | No distinct write-token field. `prompt_cache_miss_tokens` is not a cache-write measurement. | Validate `prompt_tokens = hit + miss`. Capture reasoning tokens when supplied; keep cache-write billing `unknown`. | Cost remains unknown for cache-write applicability; provider total can still be retained. Visible message content is locally tokenized separately from `reasoning_content`. |
| DeepSeek Responses API | `usage.output_tokens` maps to provider total; reasoning subcount if response usage exposes it | `usage.input_tokens_details.cached_tokens` | No write field or TTL breakdown shown | Keep write billing unknown; do not infer writes from misses. | Cost unknown until write applicability is resolved. |

## Provider-specific evidence

- Anthropic Messages API reference: [`usage` is billing/rate-limit usage; total input is input + cache creation + cache read; cache creation is split by `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`](https://platform.claude.com/docs/en/api/messages/create).
- Anthropic prompt caching guide: defines the same total-input equation and cache read/write categories ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- OpenAI Responses API prompt caching: documents `cached_tokens`, `cache_write_tokens`, TTL breakdown `ttl_30m_tokens`/`ttl_12h_tokens`, and states the TTL counts sum to aggregate writes ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- OpenAI cache guidance states cache write price/lifetimes depend on endpoint and model family; pricing cannot be flattened across Responses and older endpoints ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- DeepSeek Chat Completions API: `prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens`; cached_tokens is described as equivalent to hit tokens ([Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)).
- DeepSeek Context Caching guide says caching is enabled by default/best effort and describes hit/miss reporting, not a separately metered write event ([Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)).
- DeepSeek Responses usage reports input/output and cached tokens, with no cache-write TTL breakdown ([Responses API](https://api-docs.deepseek.com/api/create-response/)).

## Field classification

| Value | Anthropic | OpenAI Responses | DeepSeek Chat Completions |
|---|---|---|---|
| Input/provider-output tokens | DOCUMENTED | DOCUMENTED | DOCUMENTED (`prompt_tokens`/`completion_tokens`) |
| Reasoning tokens | NOT SEPARATELY REPORTED in reviewed Messages usage; null | DOCUMENTED in output usage details | DOCUMENTED in completion usage details when present |
| Visible output tokens | LOCAL pinned-tokenizer measurement required | LOCAL pinned-tokenizer measurement required | LOCAL pinned-tokenizer measurement required from message content only |
| Cache read | DOCUMENTED | DOCUMENTED | DOCUMENTED |
| Cache write aggregate | DOCUMENTED | DOCUMENTED | NOT EXPOSED |
| Cache write lifetime split | DOCUMENTED 5m/1h | DOCUMENTED 30m/12h | NOT EXPOSED |
| Retries | Client-measured; NOT provider usage | Client-measured; NOT provider usage | Client-measured; NOT provider usage |
| Latency | Client-measured wall clock | Client-measured wall clock | Client-measured wall clock |
| Provider/HTTP call count | Client-measured | Client-measured | Client-measured |
| Pricing version/effective date | Local registry reference; not returned as usage | Local registry reference; not returned as usage | Local registry reference; not returned as usage |

“Not exposed” is not the same as zero. No cache-write usage or pricing value is inferred from cache misses, cache TTL configuration, or cache being enabled by default.

## Required follow-up before external evaluation

1. Wire the parsers through provider adapters only after separate authorization; preserve raw numeric usage fields and enforce Anthropic total-input/cache-TTL equations, OpenAI write TTL sum, and DeepSeek hit+miss sum.
2. Keep DeepSeek's cache-write billing category `unknown` until an official pricing statement proves `included_in_input` or `not_applicable`; do not turn cache misses into cache writes.
3. Keep cost `unknown` if a provider's documented response shape differs, version is not pinned, TTL detail is missing where prices differ, or provider response omits a field required by the selected pricing version.
4. In an explicitly authorized test phase, verify actual response schemas and returned model IDs with synthetic/non-sensitive prompts; this audit itself made no calls.

## Result

**DOCUMENTATION AUDIT PASS; SYNTHETIC PARSER TESTS PASS; PROVIDER-COST COMPATIBILITY PARTIAL.** Anthropic and OpenAI expose detailed cache-write telemetry in their documented APIs. The local contract supports explicit billing applicability; DeepSeek remains `unknown` because its official documentation exposes hit/miss but no write category or explicit separate-write billing statement. Parsers are not connected to providers; no provider or JEV execution is authorized by this audit.
