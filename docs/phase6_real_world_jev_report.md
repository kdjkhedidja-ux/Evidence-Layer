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

| Artifact | SHA-256 |
|---|---|
| Run scope | `958dcac08d960b60839ca371ab5caac78d135f12d58d39a67b4c499b53150965` |
| Raw candidate score outputs | `bffe9e8b9b3ab305b8906fcc4ff9d2d4c88398d325820fd1ba3f418afeedd7ee` |
| Result report | `2e7bb4d12d248a7e5a57ce8ab85cb2381f22fe2af79144012635fa21622ed7ce` |
| `phase6_freeze_manifest.json` | `0082546fed1b623a89ab032fd39b5d9aca413817867d34690a1babaa51244f31` |
| Frozen synthetic result | `9b511fb1975c93caa4e420d853430f16e8d5151e1728f7e5a6ce5a09d99f71f6` |

After the live run, all recorded input fingerprints matched their pre-run
values. The synthetic freeze manifest and synthetic results were not edited.
The saved score outputs and all metrics can be checked offline:

```bash
python scripts/verify_real_world_jev_results.py --verify
```

The API run itself is reproducible by installing the pinned optional `jev`
extra and explicitly running
`python scripts/run_real_world_jev_evaluation.py --run-authorized`. That
command makes 200 provider requests. It refuses to overwrite the saved
evidence.

## Scientific limits

* The current relevance judgments were authored by the developer. Independent
  blind adjudication has not been performed.
* The 8-case `exposed_test` split was disclosed previously. It is not blind and
  must not be used as a blind test for future JEV comparisons.
* The positive deltas are exploratory on 40 cases with developer-authored
  labels. No confidence interval or significance test was run, and no
  confirmatory superiority claim is supported.
* A new unseen benchmark and completed independent blind adjudication are
  required for a confirmatory comparison.
* This run used only the fixed public benchmark. It did not enable `/retrieve`,
  modify production Egress, deploy code, or access production Supabase or
  Railway.
