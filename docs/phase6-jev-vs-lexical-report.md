# Phase 6: JEV vs Lexical Evidence-Selection Evaluation

Status: **EXECUTION PASS** (run completed 2026-10-31; offline integrity re-verified 2026-11-06). This is an evaluation result, not a production policy change. The production provider-selection default remains lexical.

## Artifacts

| Artifact | SHA-256 check |
|---|---|
| `reports/phase6_real_world_jev.json` | matches `results_sha256` in the manifest |
| `reports/phase6_real_world_jev_manifest.json` | run id `de1f7092-f664-461b-8b35-7b57d1414de8` |
| `reports/phase6_real_world_lexical.json` | unchanged lexical baseline |

Run: 200 candidate scores (40 cases × 5 candidates), splits `dev` 110, `validation` 50, `exposed_test` 40. Model requested `jev-latest`, returned `jev-1.14.0` for all 200 calls, SDK `0.7.2`. Inputs unchanged after the run; synthetic artifacts untouched. Offline verifier `scripts/verify_real_world_jev_results.py` re-passed.

## Metrics per split

JEV vs Lexical on the frozen Real-World v1 set. Higher is better.

| Split | Cases | Arm | MAP | MRR | P@1 | recall@5 | nDCG@5 | graded nDCG@5 |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| dev | 22 | JEV | 0.912 | 0.939 | 0.909 | 1.000 | 0.941 | 0.948 |
| dev | 22 | Lexical | 0.715 | 0.733 | 0.545 | 1.000 | 0.796 | 0.862 |
| validation | 10 | JEV | 0.983 | 1.000 | 1.000 | 1.000 | 0.992 | 0.997 |
| validation | 10 | Lexical | 0.858 | 0.883 | 0.800 | 1.000 | 0.905 | 0.953 |
| exposed_test | 8 | JEV | 0.979 | 1.000 | 1.000 | 1.000 | 0.990 | 0.989 |
| exposed_test | 8 | Lexical | 0.844 | 0.854 | 0.750 | 1.000 | 0.887 | 0.920 |
| overall | 40 | JEV | 0.943 | 0.967 | 0.950 | 1.000 | 0.964 | 0.968 |
| overall | 40 | Lexical | 0.777 | 0.795 | 0.650 | 1.000 | 0.842 | 0.896 |

Paired bootstrap over `case_id` (10,000 replicates, 95%, overall):

| Metric | JEV − Lexical | 95% CI | Interpretation |
|---|---:|---|---|
| MAP | +0.166 | +0.089 to +0.250 | JEV higher; interval excludes 0 |
| MRR | +0.172 | +0.092 to +0.261 | JEV higher; interval excludes 0 |
| P@1 | +0.300 | +0.175 to +0.450 | JEV higher; interval excludes 0 |
| recall@5 | 0.000 | 0.000 to 0.000 | Both arms retrieve every gold candidate at 5; no difference |

The gain is in ranking position, not in recall: both arms reach recall@5 = 1.0, and JEV places gold candidates nearer the top.

## Operational accounting

| Field | Value |
|---|---|
| SDK calls / HTTP requests / responses | 200 / 200 / 200 |
| HTTP status | 200 for all 200 |
| Successful / failed scores | 200 / 0 |
| Retries | 0 |
| Input tokens reported | 76,184 |
| Output tokens reported | 4,400 |
| Missing token records | 0 |
| Latency | mean 143.06 ms, median 141 ms, p95 163 ms, 29.3 s total wall clock |
| Financial cost (billed) | `null`, `financial_cost_status: not reported by the System One response; no invoice/billing API queried` |
| Financial cost (estimate) | **$0.003199728** at the published list price; see the estimate section and its explicit `estimate` status |

No prompt or response body is stored: only scores, ids, content hashes, timings, token counts, model ids, and error categories.

## Cost estimate (estimate, not invoice)

The provider does not return a financial charge for this API, so no billed amount is claimed. The estimate uses the measured input tokens from the verified run and the published list price.

```text
estimated_jev_usd = (measured_input_tokens / 1_000_000) * unit_price_usd_per_million_input
                  = (76,184 / 1,000,000) * 0.042
                  = 0.003199728 USD  (~0.00320 USD)
```

| Field | Value | Status |
|---|---|---|
| Measured input tokens | 76,184 (200 calls, 0 missing) | `measured` |
| Output tokens | 4,400 | `measured`; billed at zero per published price |
| Unit price | $0.042 per million input tokens ($42 per billion) | `published`, retrieved 2026-11-06 |
| Price source | <https://docs.typesafe.ai/models> — Jev 1.14 row, "You pay per input token. Output tokens are free." | `published` |
| Returned model | `jev-1.14.0` (all 200 calls) | `measured` |
| Estimated total | **$0.003199728** | `estimate` |
| Invoiced / billed amount | `null` | not reported by the provider; no invoice queried |

Machine-readable fields (no invoice semantics):

```json
{
  "jev_run_id": "de1f7092-f664-461b-8b35-7b57d1414de8",
  "measured_input_tokens": 76184,
  "measured_output_tokens": 4400,
  "input_token_records_missing": 0,
  "model_returned": "jev-1.14.0",
  "unit_price_usd_per_million_input": 0.042,
  "price_source": "https://docs.typesafe.ai/models",
  "price_retrieved_at": "2026-11-06",
  "estimated_usd": 0.003199728,
  "invoiced_usd": null,
  "billing_status": "estimate_only"
}
```

Derived views of the same estimate:

- Per call: 380.92 input tokens → about **$0.0000160** per call (`estimate`).
- Per case (5 candidate calls): about **$0.0000800** per case (`estimate`).

**Prepaid/pack sensitivity:** none is shown. The cited documentation lists only the $42/Btok rate; no prepaid pack, credit, volume, or discount rate is documented there. Inventing one would misstate cost.

This estimate excludes the judge/evaluator cost, local compute, retries (there were none), and any provider-side minimums. It is not a billing statement and must not be added to an invoice total.

## Comparison with the synthetic baseline

Reported side by side only, never merged:

- Real-World v1 comparison: `reports/phase6_real_world_jev.json` (this report).
- Synthetic baseline: `reports/phase6_quality_evaluation.json`, frozen and unchanged (`synthetic_artifacts_touched: false`).

No combined score is computed, and the synthetic baseline was not edited.

## Execution verdict

**PASS.** The evaluation ran to completion on the frozen set with zero failures and zero retries, its artifacts verify offline, and the inputs were unchanged afterwards.

## Recommendation

1. **Keep lexical as the production default.** The evidence supports JEV as a ranking improvement, but it comes from provisional judgments on an exposed split. That is not enough for a default flip.
2. **Optional JEV for evaluation and analysis**, behind explicit policy and authorization, is supported by the numbers.
3. **Needs more data before any production decision**: an independent, blind adjudication of relevance, plus a fresh held-out split that was not previously disclosed.

## Residual risks

- Relevance judgments are developer-authored; independent blind adjudication is pending.
- `exposed_test` has been disclosed before and is not a blind test for JEV.
- Results are classified as exploratory and do not establish scientific superiority or generalization.
- `jev-latest` is a moving alias; the returned `jev-1.14.0` is recorded per call, but future runs may resolve differently.
- Financial cost is unavailable; only token counts and latency are recorded.
- Sample size is small per split (22/10/8 cases), so intervals are wide.

## Out of scope in this step

No production policy flip, no price effective-date claims, no deletion/purge work, no roster change.

The Phase 7 Anthropic paired benchmark reuses this estimate and is reported in `docs/phase7_anthropic_end_to_end.md`.
