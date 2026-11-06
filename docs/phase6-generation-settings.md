# Phase 6 Candidate Generation Settings

Status: **proposed contract; not frozen, operationally verified, or authorized**. Candidate-specific design is consolidated in `docs/phase6-operational-model-matrix-design.md`. No provider calls have been made. This document records API differences; it does not assert that vendor controls are semantically identical.

## Common experiment controls

| Control | Proposed fixed value |
|---|---|
| Endpoint role | Answer generation only; providers do not perform retrieval or JEV scoring. |
| Prompt | Same versioned system/user template, evidence order, and answer schema for each paired case. Hash rendered prompts per attempt. |
| Visible answer limit | Target at most `800` visible answer tokens, measured after separating visible text from reasoning. Over-limit answers are marked `visible_cap_violation`; do not truncate and score them as ordinary completions. |
| Hard total output limit | Provider/model-specific API cap, separate from the 800 visible-token target. **PENDING decision per candidate**; the cap includes reasoning where the provider counts reasoning as output. |
| Sampling controls | Provider-specific, documented below. Do not set unsupported parameters; omission is recorded and is not interpreted as a shared default. |
| Tools | None: no web, retrieval, function, code, or server-side tools. |
| Conversation state | Stateless one-shot request; no prior conversation/history. |
| Retry | No hidden retry. Every retry is a separately recorded attempt and stays in the denominator. |
| Data retention | Request `store=false` for OpenAI Responses. Anthropic Messages and DeepSeek Chat Completions have no equivalent request field in this profile. Provider retention policy is a separate approval; do not infer zero retention from an API flag. |
| Reasoning | Freeze model-specific setting in the candidate table below. Different providers' effort modes are not claimed equivalent. |
| Answer quality | Same external rubric and refusal categories; provider/model identity hidden from any independent grader where feasible. |

## Candidate-specific request profile

| API model ID | Candidate endpoint/protocol | Visible answer target | Hard total output cap | Reasoning setting | Context/output ceiling | Pricing reference | Special caveat |
|---|---|---:|---|---|---|---|
| `claude-opus-5-5` | Anthropic Messages API, `anthropic-version: 2026-10-21` | <=800 visible tokens | PENDING `max_tokens` | Adaptive thinking; `output_config.effort=medium` | 1M / 128K | `anthropic-claude-opus-5-5-2026-11-06` | `max_tokens` includes thinking and visible output; effort cap is behavioral, not a strict reasoning budget. |
| `claude-sonnet-5-5` | Anthropic Messages API, `anthropic-version: 2026-10-21` | <=800 visible tokens | PENDING `max_tokens` | Adaptive thinking; `output_config.effort=medium` | 1M / 128K | `anthropic-claude-sonnet-5-5-2026-11-06` | Medium is a proposed override of high default; total max includes thinking. |
| `claude-haiku-5-5` | Anthropic Messages API, `anthropic-version: 2026-10-21` | <=800 visible tokens | PENDING `max_tokens` | Adaptive thinking; `output_config.effort=medium` | 1M / 128K | `anthropic-claude-haiku-5-5-2026-11-06` | Total max includes thinking; prompt pricing changes above 100k input tokens. |
| `gpt-6.1-sol` | OpenAI Responses API `/v1/responses` | <=800 visible tokens | PENDING `max_output_tokens` | `reasoning.effort=low` proposed | 1.05M context / 922K max input / 128K max output | `openai-gpt-6.1-sol-2026-11-06` | Total max includes reasoning; set `store=false`; explicit-only cache/no breakpoints. |
| `gpt-6.1-terra` | OpenAI Responses API `/v1/responses` | <=800 visible tokens | PENDING `max_output_tokens` | `reasoning.effort=low` proposed | 1.05M context / 922K max input / 128K max output | `openai-gpt-6.1-terra-2026-11-06` | Total max includes reasoning; set `store=false`; explicit-only cache/no breakpoints. |
| `gpt-6-luna` | OpenAI Responses API `/v1/responses` | <=800 visible tokens | PENDING `max_output_tokens` | `reasoning.effort=low` proposed | 1.05M context / 922K max input / 128K max output | `openai-gpt-6-luna-2026-11-06` | Total max includes reasoning; set `store=false`; explicit-only cache/no breakpoints. |
| `deepseek-flash` | DeepSeek Chat Completions `/chat/completions` via `https://api.deepseek.com` | <=800 visible tokens | PENDING `max_tokens` | Thinking disabled; `temperature=0`, `top_p=1` proposed | 1M / 384K | `deepseek-flash-2026-11-06` | Official version `DeepSeek-V4.1-Flash-1017`; automatic cache remains on. |
| `deepseek-v4-pro` | DeepSeek Chat Completions `/chat/completions` via `https://api.deepseek.com` | <=800 visible tokens | PENDING `max_tokens` | Thinking disabled; `temperature=0`, `top_p=1` proposed | 1M / 384K | `deepseek-v4-pro-2026-11-06` | Official version `DeepSeek-V4-Pro-0813`; automatic cache remains on. |

### API-specific cache and output notes

- Anthropic API version `2026-10-21` removes temperature/top_p/top_k. Adaptive thinking stays on for Opus 5.5; effort is the available control. Its eventual `max_tokens` is a separate hard total cap and must not be set to 800 just because the visible-answer target is 800.
- OpenAI Responses `prompt_cache_options.mode="explicit"` with no breakpoints is documented to disable caching and cache writes for these GPT-6 generation requests. If counters are nonzero, reject that run configuration rather than silently accepting it as cache-free.
- DeepSeek's automatic context cache has no disable option in the reviewed Chat Completions contract. Record hit/miss counts and request order; do not claim cache-equal cost. Its cache-write billing applicability remains unknown.
- The common 800-token target applies to visible answer text, not reasoning or provider total output. Enforce it with pinned per-model tokenizers and mark violations; do not truncate. Per-provider hard total output caps remain pending.

Official source references and retrieval date appear in `docs/phase6-model-matrix.md`, `docs/phase6-pricing-registry.md`, and `docs/phase6-model-pricing-review-2026-11-05.md`.

Additional API-specific sources reviewed 2026-11-06: Anthropic [API versioning](https://platform.claude.com/docs/en/api/versioning) and [effort](https://platform.claude.com/docs/en/build-with-claude/effort); OpenAI [model endpoint pages](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); DeepSeek [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode), [Responses API compatibility](https://api-docs.deepseek.com/guides/responses_api), and [Model versions](https://api-docs.deepseek.com/quick_start/pricing).

## Fairness and freeze conditions

- Each model sees the same case, evidence-arm output, prompt template, output schema, and requested output cap.
- Within each provider/model cell, settings remain identical across Lexical and Evidence Layer evidence arms.
- Reasoning effort/mode is a provider/model treatment, not assumed equivalent across vendors. Cross-provider answer-quality comparisons must report it and must not attribute differences solely to model family.
- The 800-token cap applies to visible answer text only. Hard API output caps are separate and remain pending per candidate; the evaluator must count visible text with a pinned tokenizer and label tokenizer/version. Do not truncate an answer to force compliance.
- Cache behavior differs: Anthropic is opt-in and no cache-control block is proposed; OpenAI explicit-only mode with no breakpoints disables caching/writes for supported candidates; DeepSeek cache is automatic and cannot be disabled under the reviewed interface. Record resulting hit/miss usage and do not claim cache-equal costs.
- A model is ineligible if requested settings are unsupported or actual returned model identity cannot be established; do not silently fall back to a different ID.
- Capture requested model ID and provider-returned model ID/version separately in every run.
- No live setting is frozen until an authorized test validates the request schema and token usage fields. That validation is a provider call and remains NOT AUTHORIZED.

## Usage schema gap to close before any answer-model call

The usage schema extension is implemented locally (`docs/phase6-usage-schema-extension.md`): `provider_output_tokens`, `reasoning_tokens`, `visible_output_tokens`, tokenizer provenance, and `output_token_measurement_status` are separate fields. OpenAI and DeepSeek report reasoning counts in their documented usage schemas when present; Anthropic's reviewed Messages usage has no reasoning sub-count, so its reasoning remains `null` and is never derived from total output. Cache fields and pricing applicability remain as documented in `docs/phase6-provider-usage-compatibility-audit.md`; DeepSeek write billing remains `unknown`.
