# Phase 6 SDK Integration (roster v1)

Status: **IMPLEMENTED IN CODE; LIVE-SMOKED 2026-11-06 (8/8 candidates)**. After the account credit was restored, all eight roster candidates returned live responses and mapped correctly.

## Modules

- `src/evidence_layer/provider_sdk.py`: request builders, response mapping, opt-in client.
- `src/evidence_layer/provider_execution.py`: orchestration (unchanged policy; now passes `prompt` to clients).
- `src/evidence_layer/provider_usage.py`, `usage_measurement.py`, `output_policy.py`, `tokenization.py`: frozen usage, policy, and measurement layers, reused as-is.
- `scripts/phase6_live_smoke.py`: opt-in smoke; refuses without the flag.

Transport is `httpx` (already a declared extra). No provider SDK package is added, so request bodies are exactly the frozen profiles.

## Family adapters (one path per family)

| Family | Endpoint | Candidates | Request highlights |
|---|---|---|---|
| OpenAI Responses | `https://api.openai.com/v1/responses` | `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna` | `max_output_tokens`=8192, `reasoning.effort=low`, `store=false`, `prompt_cache_options.mode=explicit` |
| Anthropic Messages | `https://api.anthropic.com/v1/messages` | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` | `max_tokens`=8192, `anthropic-version: 2026-10-21`, `thinking.type=adaptive`, `output_config.effort=medium` |
| DeepSeek Chat Completions | `https://api.deepseek.com/chat/completions` | `deepseek-flash`, `deepseek-v4-pro` | `max_tokens`=8192, `temperature=0`, `top_p=1`, `thinking.type=disabled` |

Model IDs are the frozen roster IDs. Nothing substitutes an alias.

**Discrepancy to resolve:** the existing Phase 7 script calls DeepSeek on `/responses`. The frozen matrix specifies Chat Completions, and the usage parser follows that shape. This document keeps the frozen choice. Phase 7 is not changed.

## Response mapping

- **Returned model:** copied only from the response `model` field. If absent, `returned_model_id` stays `None` and the attempt fails with `returned_model_missing`. Mismatches fail with `returned_model_mismatch`. Nothing is relabelled.
- **Visible text:** Anthropic text blocks only; OpenAI `output_text` / message output parts; DeepSeek `choices[0].message.content`. Reasoning and `reasoning_content` are excluded.
- **Usage:** parsed by the frozen usage parsers. Reasoning is taken from the documented sub-count fields when present; Anthropic reasoning stays `null`.
- **Cap:** a length stop at or above 8192 is a success at the provider layer, then `output_cap_reached` in the orchestrator, with usage kept. A length stop below the cap is `partial_response`.
- **Shared meter:** `o200k_base` classifies visible text as `within_target` (<=800) or `over_target`. Text is never truncated.

## Safety and operations

- **Opt-in gate:** `PHASE6_LIVE_SDK` must equal exactly `1`. Without it, `LiveProviderClient.from_environment` raises and the smoke script exits with code 2 and makes no network call.
- **Keys:** read from the environment per family; never placed in a body, reply, error, repr, or output. Tests check this.
- **Transport:** one HTTP request per call, no internal retries (the orchestrator owns retries and records every attempt). Timeout 120 s; `trust_env=False`; redirects off.
- **Errors:** mapped to fixed categories (`timeout`, `transport_error`, `request_rejected`, `unknown_error`). Provider error text and exception messages never surface.
- **Tests:** `tests/test_provider_sdk.py` uses `httpx.MockTransport` throughout. No test contacts a provider.

## Live smoke (only when authorized)

Required environment variables:

- `PHASE6_LIVE_SDK=1` (opt-in flag)
- `OPENAI_API_KEY` for `gpt-6.1-*` and `gpt-6-luna`
- `ANTHROPIC_API_KEY` for `claude-*`
- `DEEPSEEK_API_KEY` for `deepseek-*`

Command, per candidate or all eight:

```sh
PHASE6_LIVE_SDK=1 python scripts/phase6_live_smoke.py --model gpt-6.1-sol
```

The smoke prints one JSON line per candidate: outcome, requested and returned model, token counts, visible status, cap status, and answer hash. It never prints answer text. Record the output as a live artifact before updating any PENDING field.

## What the offline tests do and do not prove

They prove that requests are built to the frozen shapes, that responses map to the frozen usage and policy fields, and that errors fail closed and stay sanitized. They do not prove that any provider accepts these request bodies, returns these field names, or reports these usage fields. Those facts require a recorded live run.

## Live smoke result — 2026-11-06

Complete artifact: `docs/phase6-live-smoke-2026-11-06-full.jsonl` (SHA-256 `ca8e1f03b546bb7f05f7bde775e565fa0fed1f8d9155889eea75da408f415d60`). It records statuses, counts, model identities, and answer hashes only. It contains no prompt, answer text, or credential. An earlier partial artifact (`docs/phase6-live-smoke-2026-11-06.jsonl`) captured the pre-credit state and is kept as history.

| Candidate | Live outcome | Returned model | Provider output | Reasoning | Visible (shared meter) |
|---|---|---|---:|---|---:|
| `claude-opus-5-5` | success | `claude-opus-5-5` | 27 | `null` | 13 |
| `claude-sonnet-5-5` | success | `claude-sonnet-5-5` | 31 | `null` | 19 |
| `claude-haiku-5-5` | success | `claude-haiku-5-5` | 44 | `null` | 31 |
| `gpt-6.1-sol` | success | `gpt-6.1-sol` | 10 | 0 | 6 |
| `gpt-6.1-terra` | success | `gpt-6.1-terra` | 10 | 0 | 6 |
| `gpt-6-luna` | success | `gpt-6-luna` | 10 | 0 | 6 |
| `deepseek-flash` | success | `deepseek-flash` | 26 | `null` | 26 |
| `deepseek-v4-pro` | success | `deepseek-v4-pro` | 10 | `null` | 10 |

What this proves:

- All three request shapes (OpenAI Responses, Anthropic Messages, DeepSeek Chat Completions) are accepted as sent; no `request_rejected` occurred.
- `returned_model` equals the requested roster ID for all eight candidates; nothing was relabelled.
- All three response families parse through the frozen usage layer.
- The shared `o200k_base` meter produces visible counts within target for every answer.
- Anthropic reports total output with no reasoning sub-count, so its `reasoning_tokens` stays `null`. OpenAI reports an explicit `0` for these short answers, which is recorded as reported rather than inferred. DeepSeek omits the field here, so it stays `null`.
- Provider failures would map to a sanitized category with HTTP status and provider error code, never provider message text.

What it does not prove:

- Behaviour at the 8,192 cap, at the 800 boundary, or on long outputs: these were short live answers.
- Anything about price effective dates.
- That a live long-output run reproduces the synthetic stress behaviour.

## Residual blockers before live matrix evidence

1. A live long-output run to exercise the 800 boundary and the 8,192 cap on real answers (all eight candidates are live-validated on short answers).
2. Verification that `prompt_cache_options` and `thinking`/`output_config` fields are accepted as sent (a rejected field returns `request_rejected`).
3. Confirmed price effective dates (unchanged).
4. Human confirmation of the DeepSeek endpoint choice given the Phase 7 `/responses` usage.
