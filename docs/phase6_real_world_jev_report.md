# Phase 6 — Real-World v1 JEV Run Report

**Run date:** 2026-10-31 UTC
**Classification:** exploratory; not independent blind evidence
**Run ID:** `de1f7092-f664-461b-8b35-7b57d1414de8`

## Outcome

The complete fixed set ran successfully: 51 source passages, 40 cases, 200
candidates, 200 HTTP requests, 200 HTTP 200 responses, 200 valid scores, and
no errors. No case, candidate, query, or relevance grade was removed or
changed. Each candidate was sent in a separate request with the case query.
Relevance grades were not sent to the provider.

JEV's overall MAP was **0.943**, compared with **0.777** for the frozen lexical
baseline (delta **+0.166**). Overall MRR was **0.967** versus **0.795** (delta
**+0.172**), and Precision@1 was **0.950** versus **0.650** (delta **+0.300**).
Recall@5 was **1.000** for both systems. These are descriptive measurements
against developer-authored judgments, not evidence that JEV is generally
superior.

## Metrics by split

Each delta is JEV minus Lexical. Values are macro averages over every case in
the split. The JSON result artifact retains full precision and per-candidate
scores.

| Split | Metric | JEV | Lexical | Delta |
|---|---|---:|---:|---:|
| dev (22) | MAP | 0.912 | 0.715 | +0.196 |
| dev (22) | MRR | 0.939 | 0.733 | +0.207 |
| dev (22) | Precision@1 | 0.909 | 0.545 | +0.364 |
| dev (22) | Recall@5 | 1.000 | 1.000 | +0.000 |
| dev (22) | nDCG@3 | 0.920 | 0.707 | +0.213 |
| dev (22) | nDCG@5 | 0.941 | 0.796 | +0.145 |
| dev (22) | Graded nDCG@5 | 0.948 | 0.862 | +0.086 |
| validation (10) | MAP | 0.983 | 0.858 | +0.125 |
| validation (10) | MRR | 1.000 | 0.883 | +0.117 |
| validation (10) | Precision@1 | 1.000 | 0.800 | +0.200 |
| validation (10) | Recall@5 | 1.000 | 1.000 | +0.000 |
| validation (10) | nDCG@3 | 0.992 | 0.864 | +0.128 |
| validation (10) | nDCG@5 | 0.992 | 0.905 | +0.087 |
| validation (10) | Graded nDCG@5 | 0.997 | 0.953 | +0.044 |
| exposed_test (8) | MAP | 0.979 | 0.844 | +0.135 |
| exposed_test (8) | MRR | 1.000 | 0.854 | +0.146 |
| exposed_test (8) | Precision@1 | 1.000 | 0.750 | +0.250 |
| exposed_test (8) | Recall@5 | 1.000 | 1.000 | +0.000 |
| exposed_test (8) | nDCG@3 | 0.990 | 0.862 | +0.128 |
| exposed_test (8) | nDCG@5 | 0.990 | 0.887 | +0.103 |
| exposed_test (8) | Graded nDCG@5 | 0.989 | 0.920 | +0.069 |
| overall (40) | MAP | 0.943 | 0.777 | +0.166 |
| overall (40) | MRR | 0.967 | 0.795 | +0.172 |
| overall (40) | Precision@1 | 0.950 | 0.650 | +0.300 |
| overall (40) | Recall@5 | 1.000 | 1.000 | +0.000 |
| overall (40) | nDCG@3 | 0.952 | 0.777 | +0.175 |
| overall (40) | nDCG@5 | 0.964 | 0.842 | +0.122 |
| overall (40) | Graded nDCG@5 | 0.968 | 0.896 | +0.072 |

## Paired case-level bootstrap (descriptive)

For each split, 10,000 bootstrap replicates resample `case_id` values with
replacement. Each sampled case keeps its JEV and Lexical results together; the
unit is the case, never an individual candidate. The table reports the observed
mean per-case delta and the percentile 95% interval. Seeds are fixed per split
in the result JSON, which also retains full precision and all intervals.

| Split | Metric | Delta (JEV − Lexical) | 95% percentile interval |
|---|---|---:|---:|
| dev (22) | MAP | +0.196 | [+0.092, +0.309] |
| dev (22) | MRR | +0.207 | [+0.089, +0.332] |
| dev (22) | Precision@1 | +0.364 | [+0.182, +0.591] |
| dev (22) | Recall@5 | +0.000 | [+0.000, +0.000] |
| dev (22) | nDCG@3 | +0.213 | [+0.106, +0.333] |
| dev (22) | nDCG@5 | +0.145 | [+0.066, +0.230] |
| dev (22) | Graded nDCG@5 | +0.086 | [+0.034, +0.143] |
| validation (10) | MAP | +0.125 | [+0.000, +0.283] |
| validation (10) | MRR | +0.117 | [+0.000, +0.283] |
| validation (10) | Precision@1 | +0.200 | [+0.000, +0.500] |
| validation (10) | Recall@5 | +0.000 | [+0.000, +0.000] |
| validation (10) | nDCG@3 | +0.128 | [+0.023, +0.243] |
| validation (10) | nDCG@5 | +0.087 | [-0.002, +0.203] |
| validation (10) | Graded nDCG@5 | +0.044 | [+0.003, +0.098] |
| exposed_test (8) | MAP | +0.135 | [-0.031, +0.333] |
| exposed_test (8) | MRR | +0.146 | [+0.000, +0.333] |
| exposed_test (8) | Precision@1 | +0.250 | [+0.000, +0.500] |
| exposed_test (8) | Recall@5 | +0.000 | [+0.000, +0.000] |
| exposed_test (8) | nDCG@3 | +0.128 | [-0.001, +0.270] |
| exposed_test (8) | nDCG@5 | +0.103 | [-0.016, +0.242] |
| exposed_test (8) | Graded nDCG@5 | +0.069 | [+0.002, +0.147] |
| overall (40) | MAP | +0.166 | [+0.089, +0.250] |
| overall (40) | MRR | +0.172 | [+0.092, +0.261] |
| overall (40) | Precision@1 | +0.300 | [+0.175, +0.450] |
| overall (40) | Recall@5 | +0.000 | [+0.000, +0.000] |
| overall (40) | nDCG@3 | +0.175 | [+0.103, +0.253] |
| overall (40) | nDCG@5 | +0.122 | [+0.066, +0.184] |
| overall (40) | Graded nDCG@5 | +0.072 | [+0.038, +0.110] |

These intervals describe case-resampling variability for this labeled set; they
are not significance tests or confirmatory evidence. The exposed-test MAP
interval includes zero, and all splits remain subject to the labeling and
exposure limits below.

## Run details

| Setting | Value |
|---|---|
| Requested model | `jev-latest` |
| Model returned by provider | `jev-1.14.0` (all 200 responses) |
| SDK | `typesafe-sdk==0.7.2` |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Authentication | HTTP Bearer token from `TYPESAFE_API_KEY`; key was not printed or saved |
| Prompt | One Noul question per candidate, scored from 0 to 1; prompt version `jev-real-world-relevance-noul-v1` |
| Prompt SHA-256 | `b036da9b3ea11fd877fb01e6461378b66b43299d5d024055ccacf24b200d331e` |
| Ranking | Score descending, then `candidate_id` ascending |
| Retries / concurrency | 0 SDK retries; sequential requests |
| Response settings | No temperature or other generation overrides supplied by the SDK request |
| Input tokens | 76,184 reported |
| Output tokens | 4,400 reported |
| Financial cost | Not available in the System One response; no billing API was queried |
| Latency | Mean 143 ms, median 141 ms, p95 163 ms; total run 29.32 s |
| Run interval | 2026-10-31 13:21:18.416–13:21:47.739 UTC |

The API returned token counts but did not return a financial charge. No USD
invoice amount was available. For context only, applying TypeSafe's published
rate of $0.042 per million input tokens and free output gives a list-price
estimate of **$0.003199728** (`76,184 × $0.042 / 1,000,000`); this is not an
account-reported charge or invoice, and account-specific terms may differ.
See [TypeSafe's published Jev pricing](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

## Integrity and reproduction

The real-world verification manifest, source inputs, generated case digest,
Lexical report, JEV scorer and runner, dependency lock, synthetic freeze
manifest, and frozen synthetic results are fingerprinted in the independent
JEV run manifest. The saved report contains per-candidate scores, model IDs,
token counts, latency, failure categories, and query/candidate text hashes; it
does not contain API credentials or request/response bodies.

The offline verifier requires the exact same 40 `case_id` values and 200
`(split, case_id, candidate_id)` records for JEV and Lexical. It checks saved
query and candidate-text hashes against those fixed records, then recomputes
each Lexical per-case metric from those same five candidates before comparing
the saved per-case metrics. The verifier also fingerprints its own analysis
script in the independent manifest.

| Artifact | SHA-256 |
|---|---|
| Run scope | `958dcac08d960b60839ca371ab5caac78d135f12d58d39a67b4c499b53150965` |
| Raw candidate score outputs | `bffe9e8b9b3ab305b8906fcc4ff9d2d4c88398d325820fd1ba3f418afeedd7ee` |
| Result report | `607718aee6bda8a7526910fb27c574e370ce21fe00795a63c27c00a76489ccde` |
| `phase6_freeze_manifest.json` | `0082546fed1b623a89ab032fd39b5d9aca413817867d34690a1babaa51244f31` |
| Frozen synthetic result | `9b511fb1975c93caa4e420d853430f16e8d5151e1728f7e5a6ce5a09d99f71f6` |

After the live run, all recorded input fingerprints matched their pre-run
values. The synthetic freeze manifest and synthetic results were not edited.
The saved score outputs and all metrics can be checked offline. In a fresh
checkout, materialize the ignored deterministic splits first:

```bash
python scripts/build_real_world_dataset.py
python scripts/verify_real_world_jev_results.py --verify
```

The API run itself is reproducible by installing the pinned optional `jev`
extra. In a fresh checkout, first run `python scripts/build_real_world_dataset.py`
if the ignored split files are absent, then run
`python scripts/run_real_world_jev_evaluation.py --run-authorized`. The builder
writes only ignored derived split files. The API command makes 200 provider
requests and refuses to overwrite the saved evidence.

## Scientific limits

* The current relevance judgments were authored by the developer. Independent
  blind adjudication has not been performed.
* The 8-case `exposed_test` split was disclosed previously. It is not blind and
  must not be used as a blind test for future JEV comparisons.
* The positive overall deltas and case-level bootstrap intervals are
  exploratory on 40 cases with developer-authored labels. No significance test
  was run, and no confirmatory superiority claim is supported.
* A new unseen benchmark and completed independent blind adjudication are
  required for a confirmatory comparison.
* This run used only the fixed public benchmark. It did not enable `/retrieve`,
  modify production Egress, deploy code, or access production Supabase or
  Railway.
