# Phase 6 Offline Fake End-to-End Pipeline

Status: **PASS for local synthetic pipeline integrity and replay**. This is not an answer-quality benchmark, actual-cost measurement, provider interoperability test, or external evaluation.

## Pipeline

`scripts/run_phase6_fake_e2e.py` composes:

1. Frozen Real-World v1 case loading and the local `lexical_rag` and `evidence_layer` evidence arms from `run_phase6_offline_arms.py`.
2. Three synthetic answer model profiles (`fake-claude-a`, `fake-gpt-a`, `fake-deepseek-a`) through the fake `ProviderClient` protocol.
3. Provider-specific parsers, per-attempt tracking, synthetic usage/cost estimates, and exact `pricing_version` replay.
4. A content-free run manifest with dataset/config fingerprints, Git SHA, virtual model/pricing identities, artifact hashes, and explicit `network=none`, `jev=not_run`, `production=false`.

The fake clients receive only run/case/arm/model IDs and derive deterministic synthetic usage/answer output. No real question, evidence, answer model, provider SDK, credential, or network request is used in these fake calls. The synthetic answer text is not persisted; only its hash is included in answer rows.

## Attempt and result accounting

- 40 benchmark cases produce 80 evidence results (40 per evidence arm).
- Three virtual models are evaluated over both arms: 240 answer-result rows.
- One deterministic timeout is retried, producing 241 network-attempt simulations and 241 answer Usage events.
- Combined Usage log contains 80 evidence-stage events plus 241 synthetic answer-attempt events: 321 total.
- Every answer row links to its attempt IDs and ordinals. Every attempt has an individual UsageEvent and cost outcome.
- Successful fake token usage is `estimated`, never `actual`. The timeout attempt has unknown/null usage and cost. DeepSeek fake costs remain unknown under unknown cache-write billing applicability.
- Synthetic price snapshots are marked `synthetic-fixtures-not-registry-prices`; they are included only so the pipeline can verify serialization and recomputation. They are not candidate or registry prices.
- `quality_status=not_evaluated_fake_answer`; no correctness or evidence-grounding claim is generated.

## Reproducibility evidence

The CLI ran twice with `run_id=phase6-fake-e2e-repro` to separate `/tmp` output directories. `manifest.json`, `evidence_results.json`, `answer_results.json`, `usage_events.jsonl`, and `attempts.jsonl` were byte-identical. Each artifact hash in the manifest was verified by the test. Costs were recomputed from each serialized UsageEvent and its exact synthetic PricingSnapshot version.

Automated coverage is in `tests/test_phase6_fake_e2e.py`, `tests/test_provider_execution.py`, `tests/test_provider_usage_adapters.py`, and `tests/test_usage_measurement.py`.

## Non-claims and gates

- Does not compare real model answer quality; fake output quality is not evaluated.
- Does not establish actual provider token usage, cache behavior, prices, latency, or cost.
- Does not prove returned model IDs or SDK response compatibility against live providers.
- Does not call JEV or any provider and does not authorize egress.
- Does not alter historical Real-World v1 artifacts, Production, or database migrations.

Before real provider integration, obtain explicit data-transfer authorization, freeze the Model Matrix/Pricing Registry, add provider response identity/attempt provenance to the final run schema, and validate actual response fields with approved synthetic/non-sensitive calls. DeepSeek write-billing policy remains unknown.
