# Phase 6 Pricing Registry

Status: **8 candidate snapshots accepted as references; registry not approved**. No row is approved for provider execution, billing, or a production savings claim.

## Required fields

| Field | Meaning |
|---|---|
| `pricing_version` | Immutable identifier, e.g. `provider-model-2026-11-05-v1`. |
| `provider_id`, `model_id` | Exact provider/model identity. |
| `currency` | ISO currency, normally USD for benchmark comparison. |
| `input_per_million`, `output_per_million`, `cached_input_per_million` | Numeric prices or null when unpublished; distinguish cache-read from cache-write usage. |
| `cache_write_5m_per_million`, `cache_write_1h_per_million` | Separate cache-write prices when the provider exposes them; never merge into input or cache-read. |
| `effective_at`, `retrieved_at` | Pricing validity and evidence timestamps. |
| `source_uri` | Public pricing source or private approved evidence reference. |
| `region`, `long_context_rules` | Scope and conditional pricing details. |
| `status` | `candidate`, `approved_test`, `approved_production`, `expired`. |

## Reviewed candidate pricing snapshots

Rows below are `candidate` snapshots only. They are not approved for provider calls, production, billing, or savings claims.

| Pricing version | Model | Effective date | Input / MTok | Output / MTok | Cache read / MTok | Cache writes / MTok | Context/condition | Source |
|---|---|---|---:|---:|---:|---:|---|---|
| `anthropic-claude-opus-5-5-2026-11-06` | `claude-opus-5-5` | unknown | 4.00 | 20.00 | 0.20 | 5m: 5.00; 1h: 8.00 | 1M context; standard first-party API; regional multiplier separate | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `anthropic-claude-sonnet-5-5-2026-11-06` | `claude-sonnet-5-5` | unknown | 2.00 | 10.00 | 0.10 | 5m: 2.50; 1h: 4.00 | 1M context; standard first-party API; regional multiplier separate | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `anthropic-claude-haiku-5-5-2026-11-06` | `claude-haiku-5-5` | unknown | ≤100k prompt: 0.10; >100k: 0.50 | ≤100k: 0.50; >100k: 2.50 | ≤100k: 0.01; >100k: 0.05 | ≤100k 5m: 0.125, 1h: 0.20; >100k 5m: 0.625, 1h: 1.00 | Request-level prompt threshold; count all input incl. cache read/write | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `openai-gpt-6.1-sol-2026-11-06` | `gpt-6.1-sol` | unknown | 2.00 | 10.00 | 0.10 | 30m: 2.50; 12h: 5.00 | Standard, short context ≤272k; long-context/processing tier separate | [OpenAI pricing](https://platform.openai.com/api/docs/pricing) |
| `openai-gpt-6.1-terra-2026-11-06` | `gpt-6.1-terra` | unknown | 1.00 | 5.00 | 0.05 | 30m: 1.25; 12h: 2.50 | Standard, short context ≤272k; long-context/processing tier separate | [OpenAI pricing](https://platform.openai.com/api/docs/pricing) |
| `openai-gpt-6-luna-2026-11-06` | `gpt-6-luna` | unknown | 0.10 | 0.50 | 0.01 | 30m: 0.125; 12h: 0.25 | Standard, short context ≤272k; long-context/processing tier separate | [OpenAI pricing](https://platform.openai.com/api/docs/pricing) |
| `deepseek-flash-2026-11-06` | `deepseek-flash` | unknown | 0.15 off-peak / 0.30 peak | 0.60 off-peak / 1.20 peak | 0.003 off-peak / 0.006 peak | null | Peak schedule must be recorded per run | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) |
| `deepseek-v4-pro-2026-11-06` | `deepseek-v4-pro` | unknown | 0.66 off-peak / 1.32 peak | 1.98 off-peak / 3.96 peak | 0.022 off-peak / 0.044 peak | null | Peak schedule must be recorded per run | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) |

`retrieved_at=2026-11-06`, `effective_at=unknown`, `currency=USD`, and `status=candidate` apply to every row. Anthropic and OpenAI values are first-party standard token prices; OpenAI rows distinguish short context, while long-context rates are not substituted. Anthropic cache writes are distinct 5m/1h prices; Haiku has a prompt-length tier. DeepSeek peak/off-peak is time-dependent and cache writes are not stated in the source, so remain `null`. Batch/Fast/Flex, regional, private offer, and provider-negotiated rates are excluded from these candidate snapshots. No row is `approved_test` or `approved_production`.

Cache-write billing policy is explicit per pricing version: Anthropic `5m`/`1h` are `separately_metered`, `30m`/`12h` are `not_applicable`; OpenAI Responses `30m`/`12h` are `separately_metered`, `5m`/`1h` are `not_applicable`; DeepSeek write billing is `unknown` for every TTL pending explicit official billing semantics. Do not convert DeepSeek's cache miss count to cache writes or mark writes as `included_in_input` without evidence.

## Rules

- Prices are snapshots, not retroactive edits to historical runs.
- Cached tokens and input/output tokens are separate dimensions.
- Cache reads and cache writes are separate; a usage event that cannot distinguish them cannot be fully costed for a provider with distinct write charges.
- Unknown JEV, retrieval, runtime, or retry cost prevents a complete savings claim.
- Pricing changes create a new version and require forecast recalibration.
- This registry is evaluation metadata, not customer billing configuration.
