# Phase 6 Provider Usage Adapters (Offline)

Status: **PASS for synthetic payload parsing only**. These parsers accept already-received JSON objects and perform no network requests. They are not connected to provider clients and do not authorize external evaluation.

## Parsers

`src/evidence_layer/provider_usage.py` exports:

- `parse_anthropic_usage(response, identity)` for Anthropic Messages usage.
- `parse_openai_responses_usage(response, identity)` for OpenAI Responses usage.
- `parse_deepseek_chat_usage(response, identity)` for DeepSeek Chat Completions usage.

The caller provides local run/case/model/attempt identity and a pinned pricing version. Parsers emit `UsageEvent` and retain unknown fields as `None`; malformed numeric counters or inconsistent documented sums raise `ValueError`.

## Normalization invariants

- **Anthropic:** normalized total input equals uncached `input_tokens` + cache reads + cache creation. 5m/1h creation counts map to those durations. The TTL split must equal aggregate `cache_creation_input_tokens`. The provider has no 30m/12h write category in this selected API mapping.
- **OpenAI Responses:** input tokens are already total input. Cache-read plus separately metered TTL writes are subsets of total input. 30m+12h write tokens must equal aggregate `cache_write_tokens`; missing TTL detail leaves write counts null, and mismatch rejects the payload.
- **DeepSeek Chat Completions:** prompt tokens must equal cache hits + misses. Hits map to cached input; misses are validated but are not treated as cache writes. All write counters stay null and token counts can be actual while cost remains unknown under the currently unknown write-billing applicability.

## Synthetic tests

`tests/test_provider_usage_adapters.py` uses synthetic dictionaries only and verifies each valid mapping, absent write TTL details, Anthropic/OpenAI aggregate mismatch, DeepSeek hit/miss mismatch, and the rule that DeepSeek write counters are not inferred from misses.

No raw provider response fixture contains real user content, credentials, or captured live traffic. Provider API calls remain unauthorized.

Future client wiring boundaries are specified in `docs/phase6-provider-adapter-integration-design.md`; that design identifies additional attempt metadata still required before connecting SDK clients.
