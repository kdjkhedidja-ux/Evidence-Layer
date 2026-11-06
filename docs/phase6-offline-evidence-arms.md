# Phase 6 Offline Evidence Arms Integration

Status: **PASS for local Lexical + Evidence Layer integration**. No provider, JEV, network, migration, or Production operation is enabled by this runner.

## Runner

`scripts/run_phase6_offline_arms.py` runs the frozen Real-World v1 cases through two explicit arms:

- `lexical_rag`: deterministic lexical ranking, top-five selection, and per-case retrieval usage event.
- `evidence_layer`: deterministic `DocumentationRAGAdapter` selection over the same lexical-ranked candidates, with the fixed Phase 6 offline policy (`max_evidence=3`, `context_budget_tokens=120`, `policy_version=phase6-offline-v1`). It records selection decision, reason codes, and before/after estimated context units.

This is an offline evidence-arm integration, not an answer-model evaluation. There is no answer model, JEV arm, provider, or financial pricing reference. The runner explicitly emits `network=none`, `jev=not_run`, `answer_model=null`, and empty `pricing_references` in the manifest.

## Run manifest

Each output directory contains:

- `manifest.json`: run ID, benchmark identity, dataset fingerprint, configuration fingerprint, git SHA when available, arm state, JEV state, pricing references, and result/usage hashes.
- `results.json`: 40 case results per arm, selected IDs, ranking IDs, evidence decision/context fields, and retrieval metrics.
- `usage_events.jsonl`: one metadata-only UsageEvent per arm/case with stage, call/retry counts, estimated offline context units, explicit zero cache-read/write counts for these local operations, `cost_usd=null`, `cost_status=unknown`, and `missing_cost_reason=pricing_missing`; no user content.

Use an explicit output directory outside the repository, for example:

```sh
PYTHONPATH=src python scripts/run_phase6_offline_arms.py \
  --output-dir /tmp/phase6-offline-run \
  --run-id phase6-offline-real-world-v1
```

The runner refuses to overwrite an existing output directory. Results do not write to `reports/` or any frozen Real-World v1 artifact.

## Reproducibility evidence

The same run was executed twice with `run_id=phase6-offline-repro`. Both runs produced:

- 80 result rows: 40 `lexical_rag` and 40 `evidence_layer`.
- 80 usage events.
- byte-identical `results.json`, `usage_events.jsonl`, and `manifest.json`.
- `network=none`, `jev=not_run`, and no answer model.
- all cost values remain unknown because Pricing Registry is empty; no unknown value is represented as zero.

Focused tests are in `tests/test_phase6_offline_arms.py` and `tests/test_usage_measurement.py`. The first verifies both arms, no JEV, and byte-level replay; the second verifies cost/usage accounting and unknown-value handling.

## Interpretation and limits

The Evidence Layer arm proves deterministic local selection wiring and manifest/usage linkage. It does not prove answer quality, model quality, JEV superiority, production latency, provider pricing, or a production Evidence Layer endpoint. Model Matrix and Pricing Registry remain unfrozen; external evaluation remains unauthorized.
