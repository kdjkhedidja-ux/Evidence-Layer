# Phase 6 Synthetic Long-Output Stress Tests

Status: **PASS for offline measurement and status logic**. Deterministic synthetic answers only. No provider is called; no SDK is used.

## How synthetic lengths are produced

`src/evidence_layer/synthetic_long_output.py` builds text from a fixed pool of single-token words. Each step appends the first pool word that keeps the shared `o200k_base` count at or below the target, preferring an exact landing. The result is checked against the shared meter, so `exact_visible_text(n)` has a visible count of exactly `n`. If no exact construction exists, it raises rather than returning an approximation. Generation is deterministic: the same `n` always yields the same text.

## Boundary rule at 800

Inclusive. A visible answer of 800 tokens is `within_target`. A visible answer of 801 is `over_target`. The rule is implemented once in `visible_target_status` and is used by every attempt record.

## What each status means

- **`within_target`**: visible answer is at most 800 shared-meter tokens.
- **`over_target`**: visible answer exceeds 800. The attempt is still a successful measured answer; the flag is for the analysis. The text is measured in full and never truncated to force compliance.
- **`output_cap_reached`**: provider output reached 8,192 tokens. This is a failure, not a complete answer. Its usage and cost are recorded, because the spend is real even though the answer is unusable.
- **`measurement_unavailable`**: the tokenizer could not be loaded (missing artifact or hash rejected through an I/O path). The attempt is a failure and carries no visible count; a missing measurement is never reported as a success or as zero.
- **`usage_invariant_failure`**: the tokenizer artifact failed SHA-256 verification (refused as invalid usage), or the usage payload broke an invariant. Also a failure.

## Case results

| Case | Expected | Result | Test |
|---|---|---|---|
| ~750 tokens (shared meter) | within target | PASS | `test_750_tokens_is_within_target` |
| 800 tokens | at boundary, within target | PASS | `test_800_tokens_is_the_inclusive_boundary` |
| 801 and 850 tokens | `over_target`, not truncated | PASS | `test_above_800_is_over_target_and_not_truncated` |
| Provider output reaches 8,192 | `output_cap_reached`, not a complete answer | PASS | `test_reaching_8192_is_output_cap_reached_not_a_complete_answer` |
| 8,191 (just below the cap) | still a success | PASS | `test_just_below_the_cap_is_still_a_success` |
| Tokenizer missing | fail closed for measurement | PASS | `test_missing_tokenizer_fails_closed` |
| Tokenizer hash mismatch | refuse load | PASS | `test_tokenizer_hash_mismatch_is_refused` |
| DeepSeek native telemetry | additional only, never classifier | PASS | `test_native_deepseek_telemetry_is_additional_not_the_classifier` |

## Gap versus Fake E2E

The Fake E2E generates short answers (tens of tokens), so it never reaches the target boundary or the cap. The stress tests close that gap for the classifier and the cap logic. They do not show how real answers are distributed in length. That requires authorized provider runs, which are out of scope here.

## Caveats carried from the tokenizer policy

- The shared meter is a common yardstick. Anthropic and OpenAI counts are proxies for their own tokenization.
- DeepSeek native counts are additional telemetry, pinned to the V4 family.
- Stress results measure counting and status logic on synthetic text, not the answer quality or real length distribution.
