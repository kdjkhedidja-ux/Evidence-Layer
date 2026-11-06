# Phase 6 Tokenizer Policy and Output Limits

Status: **ACCEPTED** (decisions recorded in the Phase 6 gate). Offline only: no provider or model API is called and no evaluation data leaves the machine.

## Tokenizer roles

| Role | Tokenizer | Pinned artifact (SHA-256) | Use |
|---|---|---|---|
| **Shared meter** | `o200k_base-tiktoken-0.14.0` (`tiktoken` 0.14.0) | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` | Classifies every visible answer against the 800-token target, for all eight candidates, so the target means the same thing across models. |
| Provider-native telemetry | DeepSeek V4 vendor tokenizer (`deepseek-v4-hf-tokenizers-0.23.3`) | `89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614` | Recorded in addition, for DeepSeek Flash and V4 Pro only. Never used for classification. It is pinned to the V4 family; applying it to a V4.1 Flash build is a family-level mapping, not an exact billing count for every build. |

The shared meter is a common yardstick, not the exact billing count of any provider. Anthropic and OpenAI counts from it are approximations of their own tokenization and must be presented as such.

Every result records the tokenizer ID, its SHA-256, and its role (`visible_tokenizer_role`). Native counts appear in `native_visible_output_tokens` with their own tokenizer ID.

## Integrity rules

- Each artifact is verified by SHA-256 before every load. A mismatch raises and the load is refused.
- A missing artifact fails closed. The response is recorded as `measurement_unavailable`, never as a measured success.
- Artifacts are installed by `scripts/fetch_phase6_tokenizers.py` into `EVIDENCE_TOKENIZER_DIR`, defaulting to `~/.cache/evidence-layer/tokenizers`. Downloads are static vocabulary files only.

## Output limits (`src/evidence_layer/output_policy.py`)

- **Visible-answer target: 800 tokens**, measured with the shared meter, applied to all candidates. The rule is inclusive: 800 is within target; 801 is `over_target`. Over-target answers are flagged and are never truncated.
- **Technical output cap: 8,192 tokens** for all eight candidates, sent with each request. It applies to total provider output, which includes reasoning tokens where the provider counts them. Reaching it is recorded as `output_cap_reached`, a failure, with usage kept. It is not a complete answer.
- Both numbers are fixed by the Phase 6 decisions and are not changed here. The constructor enforces that the cap stays within each documented API maximum and leaves at least 2,000 tokens of reasoning reserve above the target.

## Evidence

- `tests/test_output_policy.py`: policy validation, status logic, shared-meter coverage, SHA-256 rejection, fail-closed missing artifacts.
- `tests/test_synthetic_output_stress.py`: the six stress cases (see `docs/phase6-synthetic-long-output-stress.md`).
- Fake E2E run twice with the shared meter: all five artifacts byte-identical; 240/240 successful answers within target; DeepSeek native telemetry present, Anthropic and OpenAI native telemetry absent.

## Caveats carried forward

- Shared-meter counts for Anthropic and OpenAI are proxies for their own tokenization; reported as such.
- The DeepSeek native tokenizer is pinned to the V4 family, not verified per build.
- Synthetic answers test counting and status logic, not the length distribution of real answers.
