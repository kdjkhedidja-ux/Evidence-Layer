# Phase 7B — Live End-to-End Cost Validation

Run ID: `a85ae7cc-0d69-4213-8ea0-3abd9454f767` · completed `2026-10-31T17:40:00.041624+00:00`

## Scope and limits

This is a separate live rerun of all four arms. Each Evidence Layer arm makes fresh JEV calls for its own provider/case/candidate set; Phase 7 results are unchanged. Real-World v1 relevance grades were authored by the developer, and the exposed held-out split was disclosed earlier. This is exploratory evidence, not a blind test or independent scientific validation. The automated paired judge is not human review and uses OpenAI, which is also one of the tested generators.

Input set: 51 passages, 40 cases, 200 candidates. JEV: 400 requests; LLM answers: 160; paired judge calls: 80.
Per-query end-to-end latency runs from ranking/retrieval start through the provider answer. The Evidence Layer arm includes the five sequential JEV calls; the one-time authenticated ACL snapshot read is measured separately in `secure_source_read`.

## Per-provider results by split

All cost values below are provider API usage estimates from token usage and published prices; they are not invoice exports. `system API cost` = LLM + JEV. Automated quality metrics are 0–2 judge scores; evidence coverage uses the fixed provisional gold judgments.

| Provider | Split | Route | Correct | Complete | Grounded | Citation score | Evidence coverage | Mean input/output/total tokens | LLM API $ | JEV API $ | System API $ | Mean E2E ms | P95 E2E ms |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| openai | dev | traditional_rag | 2.0000 | 1.9091 | 2.0000 | 2.0000 | 1.0000 | 417.6818/96.7273/514.4091 | 0.0397 | 0.0000 | 0.0397 | 2300.3207 | 3335.4823 |
| openai | dev | evidence_layer | 2.0000 | 1.9091 | 2.0000 | 2.0000 | 0.9621 | 304.5000/88.1364/392.6364 | 0.0328 | 0.0018 | 0.0345 | 3030.1908 | 3887.0609 |
| openai | validation | traditional_rag | 2.0000 | 1.8000 | 2.0000 | 2.0000 | 1.0000 | 416.9000/78.4000/495.3000 | 0.0162 | 0.0000 | 0.0162 | 2104.0087 | 2835.9437 |
| openai | validation | evidence_layer | 2.0000 | 1.8000 | 2.0000 | 2.0000 | 1.0000 | 293.6000/77.2000/370.8000 | 0.0136 | 0.0008 | 0.0144 | 2968.9290 | 3808.4392 |
| openai | exposed_test | traditional_rag | 2.0000 | 2.0000 | 2.0000 | 2.0000 | 1.0000 | 415.2500/102.7500/518.0000 | 0.0149 | 0.0000 | 0.0149 | 2689.3447 | 4775.5177 |
| openai | exposed_test | evidence_layer | 2.0000 | 2.0000 | 2.0000 | 2.0000 | 1.0000 | 289.8750/87.1250/377.0000 | 0.0116 | 0.0006 | 0.0122 | 2979.9918 | 3690.4988 |
| openai | overall | traditional_rag | 2.0000 | 1.9000 | 2.0000 | 2.0000 | 1.0000 | 417.0000/93.3500/510.3500 | 0.0707 | 0.0000 | 0.0707 | 2329.0475 | 3394.2176 |
| openai | overall | evidence_layer | 2.0000 | 1.9000 | 2.0000 | 2.0000 | 0.9792 | 298.8500/85.2000/384.0500 | 0.0580 | 0.0032 | 0.0612 | 3004.8355 | 3910.5439 |
| deepseek | dev | traditional_rag | 1.9545 | 1.8636 | 1.9545 | 1.9545 | 1.0000 | 465.4091/355.4091/820.8182 | 0.0170 | 0.0000 | 0.0170 | 4338.8736 | 6710.4650 |
| deepseek | dev | evidence_layer | 2.0000 | 1.7727 | 2.0000 | 1.9545 | 0.9621 | 309.0909/282.5000/591.5909 | 0.0136 | 0.0018 | 0.0154 | 4359.9851 | 8223.3842 |
| deepseek | validation | traditional_rag | 1.9000 | 1.7000 | 1.9000 | 1.9000 | 1.0000 | 474.4000/344.9000/819.3000 | 0.0073 | 0.0000 | 0.0073 | 4294.3173 | 11574.9806 |
| deepseek | validation | evidence_layer | 2.0000 | 1.8000 | 2.0000 | 2.0000 | 1.0000 | 299.6000/225.0000/524.6000 | 0.0049 | 0.0008 | 0.0057 | 3790.9875 | 5508.4529 |
| deepseek | exposed_test | traditional_rag | 2.0000 | 1.8750 | 2.0000 | 2.0000 | 1.0000 | 427.3750/281.8750/709.2500 | 0.0048 | 0.0000 | 0.0048 | 3418.4208 | 6623.8647 |
| deepseek | exposed_test | evidence_layer | 2.0000 | 1.8750 | 2.0000 | 2.0000 | 1.0000 | 299.0000/254.6250/553.6250 | 0.0043 | 0.0006 | 0.0049 | 4068.0336 | 5648.8505 |
| deepseek | overall | traditional_rag | 1.9500 | 1.8250 | 1.9500 | 1.9500 | 1.0000 | 460.0500/338.0750/798.1250 | 0.0290 | 0.0000 | 0.0290 | 4143.6440 | 8098.0219 |
| deepseek | overall | evidence_layer | 2.0000 | 1.8000 | 2.0000 | 1.9750 | 0.9792 | 304.7000/262.5500/567.2500 | 0.0228 | 0.0032 | 0.0260 | 4159.3454 | 7226.0018 |

## Overall route comparison

Cost percentages compare the fixed 40-case run and use published token prices. Quality deltas are automated-judge/evidence metrics; the paired intervals below show uncertainty on this dataset.

| Provider | Mean tokens base → EL | LLM cost base → EL | EL JEV cost | System API cost base → EL | System cost change (EL vs base; negative saves) | E2E latency base → EL (ms) | Correctness Δ | Evidence coverage Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| openai | 510.3500 → 384.0500 | $0.0707 → $0.0580 | $0.0032 | $0.0707 → $0.0612 | -13.4544% | 2329.0475 → 3004.8355 | 0.0000 | -0.0208 |
| deepseek | 798.1250 → 567.2500 | $0.0290 → $0.0228 | $0.0032 | $0.0290 → $0.0260 | -10.4853% | 4143.6440 → 4159.3454 | 0.0500 | -0.0208 |

## Paired differences

The table reports Evidence Layer minus Traditional RAG. Confidence intervals resample paired cases, not candidates.

| Provider | Split | Metric | Δ mean | 95% paired bootstrap CI |
|---|---|---|---:|---:|
| openai | dev | correctness | 0.0000 | [0.0000, 0.0000] |
| openai | dev | completeness | 0.0000 | [-0.1364, 0.1364] |
| openai | dev | groundedness | 0.0000 | [0.0000, 0.0000] |
| openai | dev | citation_correctness | 0.0000 | [0.0000, 0.0000] |
| openai | dev | evidence_coverage | -0.0379 | [-0.0985, 0.0000] |
| openai | dev | total_tokens | -121.7727 | [-129.2739, -114.3636] |
| openai | dev | llm_cost_usd | -0.0003 | [-0.0004, -0.0003] |
| openai | dev | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| openai | dev | system_api_cost_usd | -0.0002 | [-0.0003, -0.0002] |
| openai | dev | end_to_end_latency_ms | 729.8702 | [487.0749, 1013.0382] |
| openai | validation | correctness | 0.0000 | [0.0000, 0.0000] |
| openai | validation | completeness | 0.0000 | [0.0000, 0.0000] |
| openai | validation | groundedness | 0.0000 | [0.0000, 0.0000] |
| openai | validation | citation_correctness | 0.0000 | [0.0000, 0.0000] |
| openai | validation | evidence_coverage | 0.0000 | [0.0000, 0.0000] |
| openai | validation | total_tokens | -124.5000 | [-137.6000, -112.1975] |
| openai | validation | llm_cost_usd | -0.0003 | [-0.0003, -0.0002] |
| openai | validation | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| openai | validation | system_api_cost_usd | -0.0002 | [-0.0002, -0.0002] |
| openai | validation | end_to_end_latency_ms | 864.9203 | [655.0502, 1075.7769] |
| openai | exposed_test | correctness | 0.0000 | [0.0000, 0.0000] |
| openai | exposed_test | completeness | 0.0000 | [0.0000, 0.0000] |
| openai | exposed_test | groundedness | 0.0000 | [0.0000, 0.0000] |
| openai | exposed_test | citation_correctness | 0.0000 | [0.0000, 0.0000] |
| openai | exposed_test | evidence_coverage | 0.0000 | [0.0000, 0.0000] |
| openai | exposed_test | total_tokens | -141.0000 | [-189.6250, -107.0000] |
| openai | exposed_test | llm_cost_usd | -0.0004 | [-0.0008, -0.0001] |
| openai | exposed_test | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| openai | exposed_test | system_api_cost_usd | -0.0003 | [-0.0008, -0.0001] |
| openai | exposed_test | end_to_end_latency_ms | 290.6471 | [-585.9325, 865.3053] |
| openai | overall | correctness | 0.0000 | [0.0000, 0.0000] |
| openai | overall | completeness | 0.0000 | [-0.0750, 0.0750] |
| openai | overall | groundedness | 0.0000 | [0.0000, 0.0000] |
| openai | overall | citation_correctness | 0.0000 | [0.0000, 0.0000] |
| openai | overall | evidence_coverage | -0.0208 | [-0.0542, 0.0000] |
| openai | overall | total_tokens | -126.3000 | [-137.7750, -117.2250] |
| openai | overall | llm_cost_usd | -0.0003 | [-0.0004, -0.0002] |
| openai | overall | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| openai | overall | system_api_cost_usd | -0.0002 | [-0.0003, -0.0002] |
| openai | overall | end_to_end_latency_ms | 675.7881 | [438.5122, 887.1317] |
| deepseek | dev | correctness | 0.0455 | [0.0000, 0.1364] |
| deepseek | dev | completeness | -0.0909 | [-0.2273, 0.0000] |
| deepseek | dev | groundedness | 0.0455 | [0.0000, 0.1364] |
| deepseek | dev | citation_correctness | 0.0000 | [-0.1364, 0.1364] |
| deepseek | dev | evidence_coverage | -0.0379 | [-0.0985, 0.0000] |
| deepseek | dev | total_tokens | -229.2273 | [-439.3773, -95.1807] |
| deepseek | dev | llm_cost_usd | -0.0002 | [-0.0005, 0.0000] |
| deepseek | dev | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| deepseek | dev | system_api_cost_usd | -0.0001 | [-0.0004, 0.0001] |
| deepseek | dev | end_to_end_latency_ms | 21.1115 | [-1730.3403, 1155.0664] |
| deepseek | validation | correctness | 0.1000 | [0.0000, 0.3000] |
| deepseek | validation | completeness | 0.1000 | [0.0000, 0.3000] |
| deepseek | validation | groundedness | 0.1000 | [0.0000, 0.3000] |
| deepseek | validation | citation_correctness | 0.1000 | [0.0000, 0.3000] |
| deepseek | validation | evidence_coverage | 0.0000 | [0.0000, 0.0000] |
| deepseek | validation | total_tokens | -294.7000 | [-661.1000, -79.2975] |
| deepseek | validation | llm_cost_usd | -0.0002 | [-0.0008, 0.0001] |
| deepseek | validation | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| deepseek | validation | system_api_cost_usd | -0.0002 | [-0.0007, 0.0002] |
| deepseek | validation | end_to_end_latency_ms | -503.3297 | [-3462.8983, 1254.0340] |
| deepseek | exposed_test | correctness | 0.0000 | [0.0000, 0.0000] |
| deepseek | exposed_test | completeness | 0.0000 | [0.0000, 0.0000] |
| deepseek | exposed_test | groundedness | 0.0000 | [0.0000, 0.0000] |
| deepseek | exposed_test | citation_correctness | 0.0000 | [0.0000, 0.0000] |
| deepseek | exposed_test | evidence_coverage | 0.0000 | [0.0000, 0.0000] |
| deepseek | exposed_test | total_tokens | -155.6250 | [-314.3750, -30.8719] |
| deepseek | exposed_test | llm_cost_usd | -0.0001 | [-0.0004, 0.0002] |
| deepseek | exposed_test | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| deepseek | exposed_test | system_api_cost_usd | 0.0000 | [-0.0003, 0.0003] |
| deepseek | exposed_test | end_to_end_latency_ms | 649.6127 | [-581.9820, 1484.5407] |
| deepseek | overall | correctness | 0.0500 | [0.0000, 0.1250] |
| deepseek | overall | completeness | -0.0250 | [-0.1000, 0.0500] |
| deepseek | overall | groundedness | 0.0500 | [0.0000, 0.1250] |
| deepseek | overall | citation_correctness | 0.0250 | [-0.0500, 0.1000] |
| deepseek | overall | evidence_coverage | -0.0208 | [-0.0542, 0.0000] |
| deepseek | overall | total_tokens | -230.8750 | [-384.7350, -117.5000] |
| deepseek | overall | llm_cost_usd | -0.0002 | [-0.0004, 0.0000] |
| deepseek | overall | jev_cost_usd | 0.0001 | [0.0001, 0.0001] |
| deepseek | overall | system_api_cost_usd | -0.0001 | [-0.0003, 0.0001] |
| deepseek | overall | end_to_end_latency_ms | 15.7014 | [-1225.5570, 936.1588] |

## Practical readout

These labels describe paired bootstrap intervals on this fixed, previously exposed dataset. They do not establish generalization.

| Provider | Question | Paired interval readout |
|---|---|---|
| openai | correctness | no direction established; interval includes zero |
| openai | completeness | no direction established; interval includes zero |
| openai | groundedness | no direction established; interval includes zero |
| openai | citation_correctness | no direction established; interval includes zero |
| openai | evidence_coverage | no direction established; interval includes zero |
| openai | total_tokens | lower with Evidence Layer; interval excludes zero |
| openai | system_api_cost_usd | lower with Evidence Layer; interval excludes zero |
| openai | end_to_end_latency_ms | higher with Evidence Layer; interval excludes zero |
| deepseek | correctness | no direction established; interval includes zero |
| deepseek | completeness | no direction established; interval includes zero |
| deepseek | groundedness | no direction established; interval includes zero |
| deepseek | citation_correctness | no direction established; interval includes zero |
| deepseek | evidence_coverage | no direction established; interval includes zero |
| deepseek | total_tokens | lower with Evidence Layer; interval excludes zero |
| deepseek | system_api_cost_usd | no direction established; interval includes zero |
| deepseek | end_to_end_latency_ms | no direction established; interval includes zero |

## Phase 7 comparison

These are descriptive Phase 7B-minus-Phase 7 differences over the same fixed cases. Phase 7 reused Phase 6 scores; Phase 7B made fresh JEV calls. Latency uses different measurement bases and is not a randomized time-only comparison.

| Provider | Route | Metric | Mean Phase 7B − Phase 7 |
|---|---|---|---:|
| openai | traditional_rag | correctness | 0.0500 |
| openai | traditional_rag | completeness | 0.0000 |
| openai | traditional_rag | groundedness | 0.0000 |
| openai | traditional_rag | citation_correctness | 0.0000 |
| openai | traditional_rag | evidence_coverage | 0.0000 |
| openai | traditional_rag | total_tokens | 2.6000 |
| openai | traditional_rag | llm_cost_usd | 0.0000 |
| openai | traditional_rag | end_to_end_latency_ms | 64.9789 |
| openai | evidence_layer | correctness | 0.0500 |
| openai | evidence_layer | completeness | 0.0750 |
| openai | evidence_layer | groundedness | 0.0000 |
| openai | evidence_layer | citation_correctness | 0.0000 |
| openai | evidence_layer | evidence_coverage | 0.0000 |
| openai | evidence_layer | total_tokens | -2.0500 |
| openai | evidence_layer | llm_cost_usd | -0.0000 |
| openai | evidence_layer | end_to_end_latency_ms | -55.7035 |
| deepseek | traditional_rag | correctness | -0.0500 |
| deepseek | traditional_rag | completeness | -0.0250 |
| deepseek | traditional_rag | groundedness | -0.0500 |
| deepseek | traditional_rag | citation_correctness | -0.0500 |
| deepseek | traditional_rag | evidence_coverage | 0.0000 |
| deepseek | traditional_rag | total_tokens | 69.1750 |
| deepseek | traditional_rag | llm_cost_usd | -0.0001 |
| deepseek | traditional_rag | end_to_end_latency_ms | -830.1847 |
| deepseek | evidence_layer | correctness | 0.0500 |
| deepseek | evidence_layer | completeness | -0.0750 |
| deepseek | evidence_layer | groundedness | 0.0250 |
| deepseek | evidence_layer | citation_correctness | 0.0000 |
| deepseek | evidence_layer | evidence_coverage | 0.0000 |
| deepseek | evidence_layer | total_tokens | -3.2750 |
| deepseek | evidence_layer | llm_cost_usd | -0.0001 |
| deepseek | evidence_layer | end_to_end_latency_ms | -935.0483 |

## Model and cost provenance

- OpenAI exact API model ID: `gpt-6.1-sol`; endpoint `https://api.openai.com/v1/responses`; `{'reasoning.effort': 'low', 'max_output_tokens': 800, 'service_tier': 'default', 'store': False, 'temperature': 'not set'}`.
- DeepSeek exact requested API model ID: `deepseek-v4-pro`; returned IDs: `['deepseek-v4-pro']`; endpoint `https://api.deepseek.com/responses`.
- JEV requested `jev-latest`; served IDs: `['jev-1.14.0']`; SDK `0.7.2`; endpoint `https://api.typesafe.ai/v1/systemone`.
- Published price snapshot: `2026-10-31T17:40:00.041624+00:00`. JEV price is $0.042/million input tokens with free output, computed from returned token usage. No account billing/invoice endpoint was available in the provider API schema, so this is a published-rate estimate, not the account's posted charge.
- Combined published-rate API cost estimate, including live JEV requests, all answer generations/retries, and automated judge calls: `0.665614376` USD. The account invoice total is unavailable.
- Failed request attempts: JEV 0, LLM 3, judge 0; final answer failures: 0; LLM retries: 3; unpriced HTTP 200 attempts: 0. The three DeepSeek failures were incomplete HTTP 200 responses during Traditional RAG and all were recovered by retries; their reported token use and cost remain included.
- JEV/LLM API calls ran from this isolated benchmark environment. No production Egress, `/retrieve`, Supabase production, Railway, or deployment path was touched.

## Reproduction

The API execution used runner source SHA-256 `220e81de90b547c3696bf687b3ee38dafe17aec6abe842046c5fa4c39f4c37b0`. The final artifact builder used `cbd4ee7c45c631f4343fe8c9456a9335b72d0c506763d28436df061cb6631b43`. Post-run artifact correction: overall answer-arm count and final report provenance only; benchmark answers, JEV scores, judge outputs, costs, and timings unchanged.

`python scripts/verify_phase7b_live_cost_validation.py` verifies saved input/output hashes and recomputes rankings, answer metrics, paired differences, bootstrap intervals, and costs offline without provider access.

## Official pricing and API references

- [OpenAI GPT-6.1 Sol model and pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/)
- [TypeSafe API reference](https://api.typesafe.ai/redoc)
- [TypeSafe official published JEV pricing](https://typesafe.ai/)
