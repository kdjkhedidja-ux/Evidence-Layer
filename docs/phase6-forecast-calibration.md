# Phase 6 Forecast Calibration

## Objective

Estimate future cost/performance from completed, labeled Real-World runs without presenting a false point certainty. Historical lexical/JEV artifacts remain immutable and are not recalculated into a new claim.

## Calibration data

Use completed run records grouped by evidence arm, model/provider, question type, evidence-size bucket, retry state, and outcome. Require actual provider usage where a forecast component claims measured calibration. Keep a train/calibration slice separate from a later validation slice; never tune on the final comparison result.

## Outputs

For each forecast horizon and cell, report:

- expected input/output/cached tokens
- expected calls, retries, and latency percentiles
- expected cost and interval
- sample count and pricing version range
- actual-vs-forecast error when a later measured run exists
- missing components and reasons

Forecast intervals must widen for sparse cells and distribution shifts. A model with no measured usage remains `uncalibrated`, not cheap.

## Acceptance checks

- Replay the same run manifest and reproduce measured aggregates.
- Compare forecast to a later holdout using absolute and relative error.
- Report coverage of the declared interval, not only mean error.
- Recalibrate after pricing, tokenizer, prompt, model, provider, or evidence-policy changes.
- Keep answer quality, evidence coverage, correct refusal, latency, and cost in the same comparison report.

## Non-claims

Forecast calibration does not prove provider availability, production SLOs, customer billing amounts, margin, or JEV superiority. It cannot authorize provider egress or a production default.
