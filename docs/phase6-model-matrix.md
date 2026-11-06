# Phase 6 Model Matrix

Status: **Candidate Roster v1 FROZEN (8 models); operational matrix not frozen**. The eight-model roster is the approved candidate basis. Rows remain ineligible for execution until provider, exact model/version, prompt, limits, data policy, settings, and pricing references are complete.

The operational design with official endpoint/settings/cache details is `docs/phase6-operational-model-matrix-design.md`; it is proposed and pending decisions, not an execution manifest.

## Required row schema

| Field | Rule |
|---|---|
| `model_id` | Stable repository identifier; never use a display name as identity. |
| `provider_id` | Stable provider identifier and region/data-processing class. |
| `model_version` | Provider-returned version when available; requested and returned values are separate. |
| `role` | `answer` for final answer generation; evidence scorers are separate arms. |
| `context_limit` | Declared hard limit and renderer policy. |
| `temperature_seed_policy` | Fixed or explicitly nondeterministic policy recorded per run. |
| `pricing_version` | Foreign key to the pricing registry; missing pricing is `unknown`, not zero. |
| `data_egress_status` | `closed`, `approved_test`, or `approved_production`; default is `closed`. |
| `status` | `candidate`, `approved_test`, `approved_production`, or `retired`. |
| `generation_profile` | Link to `docs/phase6-generation-settings.md`; settings remain proposed until request-schema validation is authorized. |

## Reviewed candidate matrix

| Model ID | Provider | Role | Status | Pricing | Egress |
|---|---|---|---|---|---|
| `claude-opus-5-5` | `anthropic` | answer | CANDIDATE | `anthropic-claude-opus-5-5-2026-11-06` | CLOSED |
| `claude-sonnet-5-5` | `anthropic` | answer | CANDIDATE | `anthropic-claude-sonnet-5-5-2026-11-06` | CLOSED |
| `claude-haiku-5-5` | `anthropic` | answer | CANDIDATE | `anthropic-claude-haiku-5-5-2026-11-06` | CLOSED |
| `gpt-6.1-sol` | `openai` | answer | CANDIDATE | `openai-gpt-6.1-sol-2026-11-06` | CLOSED |
| `gpt-6.1-terra` | `openai` | answer | CANDIDATE | `openai-gpt-6.1-terra-2026-11-06` | CLOSED |
| `gpt-6-luna` | `openai` | answer | CANDIDATE | `openai-gpt-6-luna-2026-11-06` | CLOSED |
| `deepseek-flash` | `deepseek` | answer | CANDIDATE | `deepseek-flash-2026-11-06` | CLOSED |
| `deepseek-v4-pro` | `deepseek` | answer | CANDIDATE | `deepseek-v4-pro-2026-11-06` | CLOSED |

Candidate capability facts reviewed from official documentation:

| Provider/model | API ID | Context | Max output | Generation policy |
|---|---|---:|---:|---|
| Anthropic Claude Opus 5.5 | `claude-opus-5-5` | 1M | 128K | thinking/effort must be frozen before execution |
| Anthropic Claude Sonnet 5.5 | `claude-sonnet-5-5` | 1M | 128K | thinking/effort must be frozen before execution |
| Anthropic Claude Haiku 5.5 | `claude-haiku-5-5` | 1M | 128K | thinking/effort must be frozen before execution |
| OpenAI GPT-6.1 Sol | `gpt-6.1-sol` | 1.05M | 128K | reasoning effort and output cap must be frozen |
| OpenAI GPT-6.1 Terra | `gpt-6.1-terra` | 1.05M | 128K | reasoning effort and output cap must be frozen |
| OpenAI GPT-6 Luna | `gpt-6-luna` | 1.05M | 128K | reasoning effort and output cap must be frozen |
| DeepSeek Flash | `deepseek-flash` | 1M | 384K | thinking/reasoning effort and output cap must be frozen |
| DeepSeek V4 Pro | `deepseek-v4-pro` | 1M | 384K | thinking/reasoning effort and output cap must be frozen |

These are **frozen candidate roster entries**, not selected vendors or enabled models. OpenAI `gpt-6-astra` is explicitly excluded. DeepSeek's official current model/pricing documentation exposes two supported model names; its retired legacy Flash names are not a valid third candidate. The approved roster has exactly 8 candidates; no ninth model is required.

Sources: Anthropic [Models overview](https://platform.claude.com/docs/en/models/overview), OpenAI [Models](https://platform.openai.com/api/docs/models), and DeepSeek [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing), retrieved 2026-11-05. No model-list API calls were made.

The accepted candidate count is **3 Anthropic + 3 OpenAI + 2 DeepSeek = 8**. Equal provider counts are not a benchmark requirement.

## Fairness controls

- Same question, evidence manifest, prompt template, output schema, refusal policy, and model settings across arms.
- No arm receives hidden relevance grades, strategy labels, or another arm's answer.
- Provider failures are recorded as failures/retries, not dropped from denominators.
- Cached usage is a separate category; unavailable usage remains null with a reason.
- Results are grouped by model/provider and evidence arm; pooled scores are secondary and never replace per-cell results.
