# Phase 6: Operational Model Matrix v1 — Local Contract

Status: **FROZEN (local contract only)**. This freeze covers the offline measurement and policy contract. It does not authorize SDK integration, live provider calls, egress, JEV, or any production use. Policy numbers, the candidate roster, and the historical benchmark artifacts are unchanged by this freeze.

## FROZEN

| Item | Value | Evidence |
|---|---|---|
| Candidate roster v1 | 8 models: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`, `deepseek-flash`, `deepseek-v4-pro`. `gpt-6-astra` excluded. | `docs/phase6-model-matrix.md` |
| Shared target meter | `o200k_base-tiktoken-0.14.0`, SHA-256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`, applied to every candidate's visible answer | `src/evidence_layer/tokenization.py`, `docs/phase6-tokenizer-and-output-limits.md` |
| Visible-answer target | 800 tokens, inclusive: 800 is `within_target`, 801 is `over_target`. Over-target answers are flagged, never truncated. | `docs/phase6-synthetic-long-output-stress.md` |
| Technical output cap | 8,192 tokens for all eight candidates. Reaching it is `output_cap_reached`: a failure, not a complete answer; usage and cost are kept. | `src/evidence_layer/output_policy.py`, `docs/phase6-synthetic-long-output-stress.md` |
| Tokenizer load rules | SHA-256 verified before every load. Mismatch refuses the load. A missing artifact fails closed as `measurement_unavailable`, never as a success or zero. | `src/evidence_layer/tokenization.py`, tests in `tests/test_synthetic_output_stress.py` |
| Usage schema | `provider_output_tokens`, `reasoning_tokens`, `visible_output_tokens` (with tokenizer ID), `output_token_measurement_status`, optional native DeepSeek telemetry. Output cost is counted once; reasoning and visible counts are subsets. | `src/evidence_layer/usage_measurement.py`, `docs/phase6-usage-schema-extension.md` |
| Local proof set | (a) Fake E2E run twice, byte-identical artifacts; (b) synthetic long-output stress tests (six cases); (c) Real-World v1 integrity verification (51 passages, 40 cases, lexical reproduced). | `docs/phase6-offline-fake-e2e-pipeline.md`, `docs/phase6-synthetic-long-output-stress.md` |

## PENDING

| Item | Why it cannot be closed locally | Needed to close |
|---|---|---|
| `returned_model` per candidate | **PROVEN for all 8 candidates** (live 2026-11-06, `docs/phase6-live-smoke-2026-11-06-full.jsonl`). Every returned ID equalled the requested roster ID. | Closed for short answers |
| Real response schema | **Observed for all three families** (OpenAI Responses, Anthropic Messages, DeepSeek Chat Completions) on short answers. Long/capped responses remain unobserved. | Live long-output run |
| SDK request shapes | **IMPLEMENTED (code) and live-accepted for all 8 candidates.** | Closed |
| Egress approval | Sending any evaluation data to a provider needs an explicit data-transfer decision. | Human data-protection and security approval per provider |
| Price effective dates | Source pages state no effective date; `effective_at` is `unknown`. | Confirmed dates from each provider's pricing record |

## Caveats

- **`o200k_base` is a proxy for Anthropic and OpenAI.** It is the shared yardstick, not those providers' own tokenizers. Their visible counts approximate their native tokenization and must be reported as proxy counts.
- **DeepSeek V4 family tokenizer is family-level.** It is pinned to the V4 family. This document does not claim it is verified as the exact V4.1 Flash build. It is recorded as additional telemetry only.
- Synthetic stress and Fake E2E text measure counting and status logic, not the length distribution of real answers.
- Anthropic counting endpoints would be provider calls and are not used.

## Evidence

- Stress tests: `docs/phase6-synthetic-long-output-stress.md`, `tests/test_synthetic_output_stress.py`
- Tokenizer policy and limits: `docs/phase6-tokenizer-and-output-limits.md`
- Usage schema: `docs/phase6-usage-schema-extension.md`
- Fake E2E: `docs/phase6-offline-fake-e2e-pipeline.md`
- Readiness gate: `docs/phase6-readiness-gate.md`


## Update: SDK integration

SDK request shapes are **IMPLEMENTED (code)** in `docs/phase6-sdk-integration.md` and live-accepted for Anthropic and DeepSeek. A live smoke was recorded on 2026-11-06 (`docs/phase6-live-smoke-2026-11-06-full.jsonl`): 8/8 successes, every `returned_model` equal to the requested roster ID. Long-output behaviour at the 800 boundary and the 8,192 cap remains **PENDING PROOF**. Policy numbers, roster, and historical artifacts are unchanged.
