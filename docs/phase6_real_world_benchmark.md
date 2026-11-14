# Phase 6 — Real-World Benchmark v1

Date: 2026-10-31. Status: built and measured (lexical). Independent of the
PostgreSQL baseline and the synthetic extension; results are never merged into
a single number with either.

## Provenance

| Publisher | Document | License |
|---|---|---|
| IETF RFC Editor | RFC 7519 (JSON Web Token) | IETF Trust Legal Provisions |
| IETF RFC Editor | RFC 7515 (JSON Web Signature) | IETF Trust Legal Provisions |
| Supabase | Auth JWTs, RLS, sessions guides | Apache License 2.0 |
| PostgreSQL Global Development Group | Row Security Policies chapter | PostgreSQL License |

Per-passage source URLs, sections, fetch date, and license references:
[`data/real_world/sources.json`](../data/real_world/sources.json) and
[`NOTICE.md`](../data/real_world/NOTICE.md). Each passage also carries its own
source URL, section, license, license reference, and `quote_status` (`verbatim`
or `paraphrase`). The Base64url passage is sourced to RFC 7515 Section 2. No
private user data or chat logs are included.

## Composition

* 51 passages, 40 cases, 5 candidates each.
* Case types: direct_factual (12), multi_source (10, incl. cross-publisher),
  ambiguous (6), near_answer (6 — several documents answer similarly),
  coverage (6 — three gold passages each).
* Every candidate in every case carries an explicit graded judgment:
  0 = not relevant, 1 = partially relevant, 2 = fully relevant. The grades
  were authored by the developer before scorer output was consulted. Passage
  text and provenance were later corrected; the grades were carried forward,
  not re-authored. No independent blind review has occurred, so treat them as
  provisional.
* Three-way split: 22 development / 10 validation / 8 held-out test, with
  gold-passage component-disjoint components under a pinned seed (20261101).
  Membership is pinned in
  [`data/real_world/manifest.json`](../data/real_world/manifest.json) and is
  public. The current held-out split is **exposed**: its case membership and
  lexical results are visible. It is not a blind test and must not be used as
  a blind test for any future JEV comparison. A new, unseen set is required
  for confirmatory comparison.

## Lexical results (offline, deterministic)

| Split | n | MAP | P@1 | R@5 | nDCG@5 | graded nDCG@5 | MRR |
|---|---|---|---|---|---|---|---|
| development | 22 | 0.715 | 0.545 | 1.000 | 0.796 | 0.862 | 0.733 |
| validation | 10 | 0.858 | 0.800 | 1.000 | 0.905 | 0.953 | 0.883 |
| exposed held-out | 8 | 0.844 | 0.750 | 1.000 | 0.887 | 0.920 | 0.854 |
| overall | 40 | 0.777 | 0.650 | 1.000 | 0.842 | 0.896 | 0.795 |

By case type (overall MAP): ambiguous 0.506, near_answer 0.736,
coverage 0.868, direct_factual 0.799, multi_source 0.883.

## Reading

* As a descriptive result on developer-authored judgments, real-world lexical
  MAP is 0.777 versus 0.973 on the frozen PostgreSQL baseline. Because the
  judgments have not been independently reviewed, this is provisional and is
  not evidence of comparative quality.
* Graded nDCG@5 (0.896) exceeds binary nDCG@5 (0.842) on the current
  developer-authored grades; treat this as exploratory until review is complete.
* **JEV on this benchmark: not run.** No real JEV scores exist for these
  cases. The exposed held-out split is unsuitable for a future blind JEV
  comparison. No JEV superiority is claimed.
* The split-to-split spread (dev 0.715 vs validation 0.858) is a reminder that
  small benchmarks are noisy. The exposed split has no confirmatory status.

## Next steps

Phase 1 and Phase 2 are now merged by
[`ADR 0011`](adr/0011-merge-phase1-validation-into-phase2-comparison.md).
Existing experiments are the retrospective evidence base; the Phase 2 blinded
A/B/Tie/Both-bad comparison is the validation surface.

1. For confirmatory claims, run the independent blind review process described
   in [`phase6_real_world_review_protocol.md`](phase6_real_world_review_protocol.md)
   on a new sealed set.
2. Grow the benchmark (more publishers, more passages) only after the review
   validates v1's judgment quality.
3. If a future JEV comparison is authorized, use a new, previously unseen
   benchmark with independently adjudicated judgments and paired confidence
   intervals. Do not use this exposed split as the blind test.

## Deterministic verification

[`data/real_world/verification_manifest.json`](../data/real_world/verification_manifest.json)
is an independent manifest for this real-world benchmark. Run
`python scripts/verify_real_world_benchmark.py --write-manifest` to regenerate
the ignored real-world split files, lexical report, and independent manifest.
Then run `python scripts/verify_real_world_benchmark.py --verify` to check all
input fingerprints, split-file bytes, and reproduced lexical results. Both
commands are offline and do not execute JEV. The synthetic freeze manifest is
separate and unchanged.
