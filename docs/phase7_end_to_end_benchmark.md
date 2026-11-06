# Phase 7 — End-to-End RAG Benchmark

**Run date:** 2026-10-31 UTC
**Status:** completed, exploratory, reproducible from saved artifacts
**Data:** Real-World v1, unchanged (51 passages, 40 cases, 200 candidate records)
**Result and manifest:** [`phase7_end_to_end_rag.json`](../reports/phase7_end_to_end_rag.json), [`phase7_end_to_end_rag_manifest.json`](../reports/phase7_end_to_end_rag_manifest.json)

## Executive readout

Evidence Layer consistently reduced the answer context and total generated tokens in both provider arms. Mean total tokens per answer fell by 121.65 for OpenAI (23.96%) and 158.425 for DeepSeek (21.73%). The paired case bootstrap intervals exclude zero for both token reductions.

The automated answer-quality scores do not show a general quality gain. OpenAI correctness and groundedness were unchanged while completeness was lower by 0.075 points on a 0–2 scale. DeepSeek correctness, groundedness, and citation rubric scores were each 0.025–0.05 points lower, while completeness was 0.025 points higher. The 95% paired intervals include zero for these small score changes. Gold evidence coverage fell from 1.000 to 0.979 in both provider arms; every generated citation label resolved, and provisional-grade citation relevance rose modestly, with intervals that include zero.

LLM generation cost estimates were lower in both arms: 15.48% for OpenAI and 13.05% for DeepSeek. DeepSeek's paired cost interval slightly crosses zero. **Total system cost is only partly determinable.** Phase 6's JEV response did not report a financial charge, and three earlier incomplete DeepSeek responses did not preserve usage. Two components now exist with different status: LLM spend is `measured` but a lower bound, and JEV is an `estimate` from measured Phase 6 tokens at the published list price. See the cost accounting section for the explicit fields. The reduced LLM cost still does not by itself prove a lower total cost after JEV.

With the Phase 6 historical JEV latency added only to the Evidence Layer arm, the estimated mean end-to-end increase was 796 ms for OpenAI and 121 ms for DeepSeek. The DeepSeek interval crosses zero. These are estimates from Phase 6 JEV timing, not live Phase 7 JEV calls. No claim of scientific validation follows from this small developer-labeled dataset and automated evaluator.

## Questions this run answers

1. **Does Evidence Layer improve answer quality?** Not established. Quality was near the rubric ceiling in both arms; the paired LLM-judge deltas are small and their confidence intervals include zero. Evidence coverage decreased slightly.
2. **Does it reduce tokens?** Yes in this run. It used about 24% fewer total tokens per OpenAI answer and 22% fewer per DeepSeek answer; the paired 95% intervals exclude zero.
3. **Does it reduce total cost?** Not established, but no longer unknown in the same way. LLM-only spend decreased at the point estimate, and an estimated JEV component is now available ($0.003199728, `estimate`, not billed); at the point estimate the LLM reduction exceeds it in both provider arms. DeepSeek's paired LLM interval reaches slightly above zero, local compute is unpriced, and the JEV amount is an estimate, so no total-cost claim follows.
4. **Are answers and citations preserved?** Citation references were valid in every answer. The automated quality scores remain high, but they do not prove human-validated correctness; evidence coverage was 2.1 percentage points lower.
5. **Does it improve both providers?** Token use decreased for both. The quality results do not establish a benefit for either provider; small negative and positive deltas vary by metric.
6. **Does the benefit outweigh added latency and complexity?** Not demonstrated. Live-JEV latency estimates add overhead; JEV spend is only an `estimate` ($0.003199728 at list price), total cost stays partly unknown, and no clear quality improvement was measured.

## Dataset and status of judgments

The experiment used the existing Real-World v1 case files without modification:

- 51 passages; 40 fixed questions; 5 candidates per question; 200 candidate records.
- Dev: 22 cases; validation: 10; exposed test: 8.
- The relevance grades and required facts are developer-authored. The exposed test split had already been disclosed. None of these results is blind held-out evidence or independent scientific validation.
- Answer quality was scored by an automated OpenAI evaluator using a fixed 0–2 rubric for correctness, completeness, groundedness, and citation correctness. It saw randomized `answer_x` / `answer_y` order without explicit route names. The different evidence sets may still reveal the route. The evaluator uses the same OpenAI model tested as a generator; it is not a human or independent evaluator.
- Citation-reference validity is computed deterministically by resolving each answer's `[E#]` IDs against the selected evidence. Citation relevance precision uses the provisional developer grades and is not an independent judgment.

## Systems compared

Each provider received the same 40 question texts and fixed five-candidate pool. Within each provider/case pair, the prompt instructions, model, and generation parameters were held fixed; the evidence selection is the intended treatment difference.

| Arm | Retrieval and context |
|---|---|
| Traditional RAG (A) | Existing deterministic `LexicalScorer`, cosine over the existing token representation, `score desc / candidate_id asc`, top 5 passed through as context. |
| Evidence Layer (B) | The saved real JEV scores from the completed Phase 6 run, verified against all 200 query/candidate hashes; same deterministic tie-break; top 3 selected passages passed as context. |

The Phase 6 JEV artifact reports 200 successful scores from `jev-1.14.0` using `typesafe-sdk 0.7.2`. Phase 7 made **zero new JEV calls** and reused those exact outputs. This avoids re-scoring the identical frozen inputs, but means the live-JEV latency is estimated from Phase 6's five per-candidate latencies, and JEV spend can only be estimated from Phase 6's measured tokens at the published list price (see cost accounting). The baseline has no JEV dependency and is assigned zero JEV latency.

The source passages were read in the isolated local Supabase integration through `SecureSnapshotReadPath`; workspace membership, source ACL, and snapshot digest checks were verified. The source hash is recorded in the result. The original checkpoint did not preserve the setup read duration, so setup latency is null and excluded from per-question latency. Per-question wall-clock aggregates sum recorded request and preparation durations; they exclude idle time between the three resumed run sessions. No production `/retrieve`, Supabase, Railway, or Egress settings were changed.

## Models, API settings, and price basis

Both exact model IDs were verified against the provider account's model-list endpoint during the run sessions and were returned by the generation responses. Model listing documentation describes the available IDs and capabilities: [OpenAI GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [DeepSeek model list](https://api-docs.deepseek.com/api/list-models/), [DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/).

| Provider | Exact API model ID returned | Endpoint | Authentication variable | Request settings |
|---|---|---|---|---|
| OpenAI | `gpt-6.1-sol` | `https://api.openai.com/v1/responses` | `OPENAI_API_KEY` | `reasoning.effort=low`, `max_output_tokens=800`, `service_tier=default`, `store=false`; temperature omitted. |
| DeepSeek | `deepseek-v4-pro` | `https://api.deepseek.com/responses` | `DEEPSEEK_API_KEY` | `reasoning.effort=low`, `max_output_tokens=800`, `store=false`; temperature omitted. The provider's current model list exposes this ID without a separate dated API ID; the run pins the exact listed/returned ID. |

The runner used direct HTTPX REST requests (no provider SDK); the installed HTTPX version and per-response served model IDs are in the manifest/result. The generation instructions are identical across each provider's two arms. No retrieval tools, web search, functions, or sampling temperature were enabled. Temperature was omitted, so provider defaults may differ; this is a residual cross-provider confound. Output cap: 800 tokens. The judge used OpenAI `gpt-6.1-sol`, `reasoning.effort=low`, a 700-token cap, strict JSON output, and randomized paired order.

Token charges are derived from provider-reported usage, separating cached, uncached, cache-write, and output tokens. OpenAI uses the standard rate for prompts below 272K input tokens: $2/M uncached input, $0.10/M cached input, $2.50/M cache write, and $10/M output ([official model pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol)). DeepSeek was run Saturday UTC, outside its published weekday peak windows; the official V4 Pro off-peak rates used were $0.66/M cache-miss input, $0.022/M cache-hit input, and $1.98/M output ([official pricing and peak schedule](https://api-docs.deepseek.com/quick_start/pricing/)). Prices were recorded as of 2026-10-31 and are not a billing statement.

## Results by split

Quality values are average automated rubric scores on 0–2. `A → B` means Traditional RAG → Evidence Layer. Evidence coverage and citation relevance precision are case averages based on developer-authored labels. Citation reference validity is deterministic. Token, cost, and latency values are mean per answer. Cost is known provider-reported LLM usage only.

### OpenAI (`gpt-6.1-sol`)

| Split (n) | Correctness /2 | Completeness /2 | Groundedness /2 | Citation rubric /2 | Gold evidence coverage | Citation refs valid | Citation relevance precision* |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev (22) | 2.000 → 2.000 | 2.000 → 1.864 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 0.962 | 1.000 → 1.000 | 0.845 → 0.871 |
| validation (10) | 2.000 → 2.000 | 1.800 → 1.800 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| exposed_test (8) | 1.750 → 1.750 | 1.750 → 1.750 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.855 → 0.958 |
| overall (40) | 1.950 → 1.950 | 1.900 → 1.825 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 0.979 | 1.000 → 1.000 | 0.886 → 0.921 |

| Split (n) | Input tokens / case | Output tokens / case | Total tokens / case | Known LLM USD / case | API latency ms | Estimated live-JEV E2E ms† |
|---|---:|---:|---:|---:|---:|---:|
| dev (22) | 417.68 → 303.23 | 95.95 → 88.91 | 513.64 → 392.14 | 0.001795 → 0.001496 | 2332 → 2271 | 2333 → 2976 |
| validation (10) | 416.90 → 293.60 | 80.30 → 81.10 | 497.20 → 374.70 | 0.001637 → 0.001398 | 2114 → 2427 | 2115 → 3171 |
| exposed_test (8) | 415.25 → 292.25 | 89.50 → 91.50 | 504.75 → 383.75 | 0.001726 → 0.001499 | 2262 → 2445 | 2262 → 3156 |
| overall (40) | 417.00 → 298.62 | 90.75 → 87.47 | 507.75 → 386.10 | 0.001741 → 0.001472 | 2264 → 2345 | 2264 → 3061 |

### DeepSeek (`deepseek-v4-pro`)

| Split (n) | Correctness /2 | Completeness /2 | Groundedness /2 | Citation rubric /2 | Gold evidence coverage | Citation refs valid | Citation relevance precision* |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev (22) | 2.000 → 1.955 | 1.864 → 1.864 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 0.962 | 1.000 → 1.000 | 0.843 → 0.867 |
| validation (10) | 2.000 → 1.900 | 1.800 → 1.800 | 2.000 → 1.900 | 2.000 → 1.900 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| exposed_test (8) | 2.000 → 2.000 | 1.875 → 2.000 | 2.000 → 2.000 | 2.000 → 2.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.871 → 0.938 |
| overall (40) | 2.000 → 1.950 | 1.850 → 1.875 | 2.000 → 1.975 | 2.000 → 1.975 | 1.000 → 0.979 | 1.000 → 1.000 | 0.888 → 0.915 |

| Split (n) | Input tokens / case | Output tokens / case | Total tokens / case | Known LLM USD / case | API latency ms | Estimated live-JEV E2E ms† |
|---|---:|---:|---:|---:|---:|---:|
| dev (22) | 449.82 → 309.09 | 316.55 → 279.14 | 766.36 → 588.23 | 0.000898 → 0.000734 | 4849 → 4828 | 4895 → 5532 |
| validation (10) | 430.70 → 299.60 | 239.00 → 263.80 | 669.70 → 563.40 | 0.000692 → 0.000720 | 5845 → 4034 | 5845 → 4778 |
| exposed_test (8) | 427.38 → 297.62 | 272.75 → 233.12 | 700.12 → 530.75 | 0.000781 → 0.000658 | 4101 → 3574 | 4101 → 4285 |
| overall (40) | 440.55 → 304.43 | 288.40 → 266.10 | 728.95 → 570.52 | 0.000823 → 0.000716 | 4948 → 4379 | 4974 → 5094 |

*Citation relevance precision compares cited passages to developer grades; see limitations above.
†For A, estimated live-JEV E2E latency equals the saved-score latency because Traditional RAG makes no JEV call. For B, it adds Phase 6 historical JEV latency for all five candidates. Phase 7 reused saved JEV values; neither E2E column is a new live JEV measurement.*

## Paired case bootstrap, overall

Deltas are Evidence Layer minus Traditional RAG. The 95% percentile intervals use 10,000 paired bootstrap replicates, resampling the 40 case IDs (both routes stay paired per sampled case), seed `20261031`. Scores are points on the 0–2 scale; token, cost, and latency deltas are per answer. No multiple-comparison adjustment was applied; intervals are exploratory.

| Measure | OpenAI delta [95% interval] | DeepSeek delta [95% interval] |
|---|---:|---:|
| Correctness /2 | 0.0000 [0.0000, 0.0000] | -0.0500 [-0.1250, 0.0000] |
| Completeness /2 | -0.0750 [-0.1750, 0.0000] | 0.0250 [0.0000, 0.0750] |
| Groundedness /2 | 0.0000 [0.0000, 0.0000] | -0.0250 [-0.0750, 0.0000] |
| Citation rubric /2 | 0.0000 [0.0000, 0.0000] | -0.0250 [-0.0750, 0.0000] |
| Gold evidence coverage | -0.0208 [-0.0542, 0.0000] | -0.0208 [-0.0542, 0.0000] |
| Citation reference validity | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| Citation relevance precision* | 0.0350 [-0.0008, 0.0768] | 0.0267 [-0.0042, 0.0617] |
| Total tokens / case | -121.6500 [-129.5750, -113.9500] | -158.4250 [-239.2775, -98.9988] |
| Known LLM USD / case | -0.0003 [-0.0003, -0.0002] | -0.0001 [-0.0002, 0.0000] |
| Generation API latency (ms) | 81.2044 [-115.6678, 305.2919] | -569.7182 [-1611.2598, 308.0361] |
| Estimated live-JEV E2E latency (ms) | 796.4705 [600.3406, 1029.7976] | 120.5650 [-937.3966, 1022.7976] |

## Requests, errors, and cost accounting

- Final outputs: 160/160 answers (40 cases × 2 routes × 2 providers), 80/80 paired automated judgments, 0 final failed answers.
- Generation API requests: OpenAI 80; DeepSeek 84 (80 answer requests plus 4 retry/recovery requests). The four DeepSeek failures were HTTP 200 responses marked incomplete; three occurred before usage preservation was added. Their usage and charges are unavailable. The later incomplete retry retained provider usage. OpenAI generation and all judge calls completed without retries or errors.
- Judge API requests: 80. Judge cost was **$0.4796125**, separately reported as evaluation overhead and excluded from serving costs.
- Model-list checks: 6 GET requests across three resumable run sessions (both providers checked per session). Total Phase 7 provider HTTP requests were 250: 164 generation attempts, 80 paired judge calls, and 6 model-list checks; JEV calls: 0. No secret values were saved or printed.
- Known answer-generation LLM cost across the 40 questions: OpenAI A `$0.069660`, B `$0.058880`; DeepSeek A `$0.032920184`, B `$0.028621956`. These amounts include captured usage for retried calls, but not the three unpriced incomplete HTTP 200 responses.
- Evidence Layer local deterministic ranking/selection incurred no separately billable API charge; local compute was not priced.
- JEV cost: Phase 6 reported a null financial charge, so nothing is billed here. Phase 7 made 0 new JEV calls and reused the same 200 saved records, so the estimate is a re-attribution of Phase 6's measured tokens, not a second charge.

  ```text
  estimated_jev_usd = (76,184 measured input tokens / 1,000,000) * $0.042 per million
                    = $0.003199728  (estimate, not invoice)
  ```

  Price source: <https://docs.typesafe.ai/models>, Jev 1.14 row; output tokens are free. Retrieved 2026-11-06. No prepaid/pack rate is documented on that page, so no pack sensitivity is shown.

- Combined per-route view. JEV applies only to route B (Evidence Layer), which reuses all five candidate scores per case; route A makes no JEV call.

| Provider | Route | Measured LLM USD | JEV USD | JEV status | Combined USD | Combined status |
|---|---|---:|---:|---|---:|---|
| OpenAI | A — Traditional RAG | 0.069660 | 0.000000 | `not_applicable` | 0.069660 | `measured LLM; lower bound` |
| OpenAI | B — Evidence Layer | 0.058880 | 0.003199728 | `estimate` | 0.062079728 | `measured LLM + estimated JEV` |
| DeepSeek | A — Traditional RAG | 0.032920184 | 0.000000 | `not_applicable` | 0.032920184 | `measured LLM; lower bound` |
| DeepSeek | B — Evidence Layer | 0.028621956 | 0.003199728 | `estimate` | 0.031821684 | `measured LLM + estimated JEV` |

  At the point estimate, route B remains below route A for both providers once the JEV estimate is included (OpenAI saves $0.010780 in LLM against $0.003200 estimated JEV; DeepSeek saves $0.004298 against the same). These are point estimates: LLM is a lower bound, JEV is list-price estimate, judge cost is excluded, local compute is unpriced, and no interval was computed for the combined figure. This is not a billing statement.
- Therefore the billed JEV and total-system amounts are `null`, and the estimated JEV and combined amounts are `estimate`, not zero and not invoiced. The reduction in LLM cost alone does not prove reduced total cost.

## Recovery and analysis corrections

The run resumed from a partial checkpoint and did not repeat completed answer calls. Three incomplete DeepSeek calls from the earlier checkpoint lacked usage fields; those cases were rerun with unchanged prompts and their first attempts remain recorded. The corrected runner retries incomplete HTTP 200 responses and stores attempt usage when returned.

A post-run count check caught a pair-assembly bug that had initially saved one judge pair. The answer-generation outputs were retained; the pairing logic was fixed and the missing 79 blinded-order judge calls were run, leaving 80/80. A separate audit corrected derived latency so only Evidence Layer includes historical JEV latency, and corrected summary aggregation so judge rubric scores and overall paired deltas are included. These are documented deterministic transformations of the saved outputs; no question, source, relevance grade, prompt, or answer was changed. The final hashes cover the corrected source, result, and manifest.

## Reproduction and verification

Offline verification makes no provider or network calls, reads no API secrets, checks all Phase 7 input/result hashes, revalidates the 200 saved JEV records and 40-case universe, reproduces lexical and JEV selections, citation metrics, usage-based costs, route-specific latency, split summaries, and paired bootstrap.

```bash
python scripts/verify_phase7_end_to_end_rag.py
pytest -q tests/test_phase7_end_to_end_rag.py
python scripts/verify_real_world_benchmark.py --verify
```

The live runner is `scripts/run_phase7_end_to_end_rag.py`. Rerunning it performs provider API calls and may incur cost; the saved JSON and offline verifier are the no-network reproduction path. The independent Phase 7 manifest fingerprints the full input set, model/prompt settings, saved result, and output hashes. No Phase 6 synthetic freeze artifact or Real-World v1 input was changed.

## Limitations

1. Only 40 fixed questions and 5 candidate records per question were evaluated. The 8-case exposed split is not blind and should not be used as a future blind JEV test.
2. Relevance judgments and required facts are developer-authored. No independent human adjudication was performed.
3. The automated evaluator is not human, is not independent of the tested OpenAI model, and may infer the route from evidence formatting/amount.
4. Evidence Layer selected 3 passages versus 5 in baseline; lower token counts are a direct consequence of smaller context. The run does not isolate whether reranking, passage filtering, or context size caused answer changes.
5. JEV scores were reused from Phase 6; Phase 7 did not measure fresh JEV latency or financial cost. Live-JEV latency is an estimate. JEV spend is an `estimate` ($0.003199728 at list price) and total system cost remains partly unknown: billed JEV is `null`, LLM spend is a lower bound, and local compute is unpriced.
6. Three incomplete provider responses from the legacy checkpoint lack usage. Known LLM costs are lower bounds by an unknown amount.
7. Paired bootstrap intervals are exploratory, with 40 cases and no multiplicity adjustment. They do not replace a newly collected blinded set or independent review.
8. Secure snapshot setup timing was not persisted in the initial checkpoint and is excluded from per-question latency.

## Post-run integrity update (2026-11-06)

The secure snapshot reader (`services/evidence-service/evidence_service/secure_snapshot_read.py`) gained account-lifecycle gate checks after this benchmark ran. The file is one of the Phase 7 input fingerprints, so its recorded hash was updated in the result and manifest, and the change is listed under `post_run_input_updates` in the manifest.

This is a security-only change to the read path. It does not alter the benchmark inputs, prompts, answers, scores, token counts, or costs reported above.
