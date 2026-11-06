# Phase 6 Usage Schema Extension

Status: **IMPLEMENTED LOCALLY; OPERATIONAL MATRIX STILL NOT FROZEN**. This extension changes only the offline measurement contract. It makes no provider call, no network request, and no change to Real-World v1 artifacts.

## Fields

| Field | Meaning | Cost use |
|---|---|---|
| `provider_output_tokens` | Provider's total output count (raw `output_tokens` / `completion_tokens`). Includes reasoning where the provider counts it. | Billed exactly once for output cost. |
| `reasoning_tokens` | Reasoning sub-count reported by the provider, or `null` when unreported. | Never added to output cost. Must be <= `provider_output_tokens`. |
| `visible_output_tokens` | Visible answer tokens measured locally from visible text with a pinned tokenizer. | Descriptive only; never added to output cost. Requires `visible_output_tokenizer_id`. |
| `visible_output_tokenizer_id` | Identifier of the tokenizer used for `visible_output_tokens`. | Provenance. |
| `output_token_measurement_status` | `provider_reported_complete` (reasoning and visible both measured), `provider_reported_partial`, `synthetic`, `not_applicable`, or `unknown`. | Provenance; a status that requires provider output must have `provider_output_tokens`. |

The legacy `output_tokens` field is removed from `UsageEvent`. Aggregates now report `provider_output_tokens`, `reasoning_tokens`, and `visible_output_tokens`; an aggregate of any field is `null` unless every event reports it.

## Provider mapping

- **Anthropic Messages:** raw `usage.output_tokens` -> `provider_output_tokens`. No reasoning sub-count is documented, so `reasoning_tokens` is `null`. Visible text is read from `content[].text` blocks of type `text` only.
- **OpenAI Responses:** raw `usage.output_tokens` -> `provider_output_tokens`; `output_tokens_details.reasoning_tokens` -> `reasoning_tokens`; visible text from `output_text` or message `output_text` parts.
- **DeepSeek Chat Completions:** raw `usage.completion_tokens` -> `provider_output_tokens`; `completion_tokens_details.reasoning_tokens` -> `reasoning_tokens` when present; visible text from `choices[0].message.content`, excluding `reasoning_content`.

Absence of a reasoning counter means *not reported*, never zero.

## Cost rule

`cost_for_event` multiplies `provider_output_tokens` by the output rate exactly once. `reasoning_tokens` and `visible_output_tokens` do not enter the formula. `tests/test_usage_measurement.py::test_reasoning_and_visible_subcounts_do_not_add_to_billed_provider_output` checks that a measured event costs the same as the unmeasured one.

## Verification

- `tests/test_usage_measurement.py`, `tests/test_provider_usage_adapters.py`, `tests/test_provider_execution.py`, `tests/test_phase6_offline_arms.py`, `tests/test_phase6_fake_e2e.py`: 38 tests pass in `/tmp/evidence-layer-phase6-venv`.
- Fake clients now emit provider-shaped payloads (Anthropic `content`, DeepSeek `choices[].message.content`, OpenAI `output_text`). Before this fix only the OpenAI shape carried visible text, so 160 of 240 successful answers had no visible measurement.
- `scripts/run_phase6_fake_e2e.py` run twice: manifest, evidence results, answer results, usage events, and attempts are byte-identical. 240/240 successful attempts carry visible tokens; reasoning is populated for OpenAI and DeepSeek and `null` for Anthropic; no reasoning count exceeds provider output.
- `scripts/verify_real_world_benchmark.py --verify`: 51 passages, 40 cases, lexical results reproduced.

## Still open before the operational matrix can freeze

- Hard total-output cap: a uniform practical 8,192 is now applied per candidate; per-model values need confirmation.
- Tokenizer policy: accepted. The shared meter (`o200k_base`) classifies all candidates; DeepSeek's vendor tokenizer is additional telemetry (`docs/phase6-tokenizer-and-output-limits.md`).
- Validation against authorized live responses of the actual reasoning/usage fields for each candidate.
- DeepSeek cache-write billing applicability, pricing effective dates, egress approval, and SDK validation (unchanged from the readiness gate).
