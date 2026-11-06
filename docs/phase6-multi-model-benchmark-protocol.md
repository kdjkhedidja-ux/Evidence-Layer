# Phase 6: Multi-Model Benchmark Protocol

Status: specification only. No production change, migration, provider-default change, or new benchmark result is authorized by this document.

The consolidated decision and stop gate is `docs/phase6-readiness-gate.md`; it distinguishes freezeable offline references from pending operational/provider approval.

## Purpose

Phase 6 separates three effects that must not be conflated:

1. **Evidence quality:** what the retrieval/evidence strategy admits, ranks, selects, and proves.
2. **Answer quality:** what the caller's answer model produces from the same approved evidence pack.
3. **Operational economics:** provider usage, calls, latency, retries, and cost.

The comparison is factorial: every evidence strategy is evaluated with every approved answer model on the same questions, candidate universe, prompt contract, and run policy.

## Frozen references and boundaries

- Real-World Benchmark v1 remains the fixed historical reference: 40 cases, 51 passages, five candidates per case (`docs/phase6_real_world_benchmark.md:23-42`).
- Its developer-authored judgments remain provisional; its exposed held-out split is not a blind confirmatory test.
- Synthetic baselines/extensions remain separate artifacts and are never pooled into one score.
- A future confirmatory run requires a new unseen set, independent review, and a sealed membership key (`docs/phase6_real_world_review_protocol.md:82-88`).
- Current live JEV egress remains closed by policy. No model/provider call is enabled by this specification.
- No billing, invoice, or production cost claim may use forecast values as actual usage.

## Experiment matrix

Evidence strategies:

- `lexical_rag`: baseline retrieval/ranking with the frozen lexical protocol.
- `jev_rag`: JEV ranking only when a separately approved transfer grant and provider run exist; otherwise `NOT_RUN`.
- `evidence_layer`: candidate validation, authorization, selection, sufficiency, provenance, and explicit budget policy. If the production evidence-pack endpoint is unavailable, this arm is `NOT_IMPLEMENTED`, not silently replaced by lexical.

Eight answer-model candidates are listed in `docs/phase6-model-matrix.md`; proposed provider-specific generation settings are in `docs/phase6-generation-settings.md`. Each model runs against each eligible evidence arm with the same rendered prompt, evidence ordering contract, refusal policy, and recorded temperature/seed policy. Provider-specific reasoning controls are not assumed equivalent. A model result cannot be credited to an evidence strategy unless the evidence arm actually ran and its manifest matches the answer request.

## Required run record

Every attempt receives a stable `run_id`, `case_id`, `arm_id`, `model_id`, `provider_id`, and `attempt_id`. The immutable run manifest records:

- dataset and split manifest hashes
- prompt, renderer, selector, scorer, tokenizer, and pricing versions
- candidate/evidence manifest hash and selected evidence IDs
- request start/end timestamps and outcome
- provider-reported input/output/cached tokens when available
- call counts, retries, errors, refusal category, and answer artifact hash
- cost values with `actual`, `estimated`, or `forecast` status

No query, passage body, answer text, API key, or secret belongs in aggregate usage logs. Detailed text stays in the approved local evaluation boundary.

## Measurements and decision rules

Evidence metrics are reported independently: candidate recall, MRR/MAP/nDCG, fact coverage, full-support rate, false-sufficient rate, and correct abstention. Answer metrics are independently reviewed: correctness, completeness, groundedness, citation support/correctness, and correct refusal. Operational metrics include total input/output tokens, provider-reported reasoning tokens when available, visible answer tokens measured with a pinned tokenizer, cache categories, calls, retries, p50/p95 latency, and cost per successful task. Missing reasoning/visible token counts remain null and are not derived by subtracting unrelated totals.

Do not declare an evidence arm better because it uses fewer tokens if answer quality, required-fact coverage, authorization, or correct-refusal quality regresses. Results are paired by case and model; uncertainty uses declared case-level bootstrap or another predeclared method. No threshold may be chosen after viewing the test result.

## Execution order

1. Verify and freeze the existing v1 artifacts offline without editing them.
2. Complete independent blind review and prepare a new sealed confirmatory set if claims beyond descriptive history are needed.
3. Register model/provider rows and pricing versions; reject incomplete rows rather than imputing zero cost.
4. Run offline lexical and Evidence Layer arms first.
5. Run JEV only after transfer authorization, provider policy, data handling, and egress review.
6. Run every approved answer model over identical arm manifests.
7. Reconcile usage, latency, quality, and cost; publish per-arm/per-model results and uncertainty.
8. Calibrate forecasts against later actual runs; do not rewrite historical results.

## Exit criteria

The protocol is implemented only when a replayable run manifest, usage events, model matrix, pricing registry, answer-quality labels, and independent verification all exist. Until then Phase 6 is a design/specification state, not a production benchmark or cost claim.

The completed JEV vs Lexical evidence-selection evaluation is reported in `docs/phase6-jev-vs-lexical-report.md` (execution PASS; lexical remains the production default).
