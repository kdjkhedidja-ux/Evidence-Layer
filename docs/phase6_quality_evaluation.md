# Phase 6 — Quality evaluation: JEV vs lexical

Date: 2026-10-31. Status: measured. No production settings changed; no egress
opened; live JEV transfer remains hard-denied on all package paths.

## Frozen evaluation set

The unified evaluation set is the full deterministic dataset from
`scripts/build_dataset.py`: **100 cases** (78 dev + 22 held-out; membership
pinned by the splitter's fixed seed), each with exactly 5 candidates and 1–4
sources (17 distinct sources overall; 60% of cases span 2+ sources).

* Lexical scores are recomputed offline for all 100 cases with the frozen
  default `LexicalScorer`. A drift check confirmed the recomputed lexical
  scores match the frozen Phase 5B run exactly (110/110 candidates).
* JEV scores are the real frozen per-candidate measurements from
  [`reports/phase5b_heldout_test_single_run.json`](../reports/phase5b_heldout_test_single_run.json),
  which cover exactly the 22 held-out records. Because live JEV transfer is
  hard-denied and egress stays closed, **the JEV-vs-lexical comparison covers
  those 22 records**; lexical-only metrics are reported on the full 100.
  Extending JEV coverage requires an authorized provider run — a separate
  decision that remains closed.

Method: per-record precision@k, recall@k, nDCG@k (k ∈ {1, 3, 5}), average
precision (full-ranking MAP), and MRR. Rankings sort score-descending with
candidate_id-ascending ties (the frozen selector behavior). Uncertainty uses a
paired record-level percentile bootstrap (10,000 replicates, linear
interpolation, fixed seed), matching the Phase 5B methodology. No superiority
was predeclared for either scorer.

Reproduce: `python scripts/run_quality_evaluation.py` (offline; writes the
gitignored `reports/phase6_quality_evaluation.json`).

## Gap analysis of the base set (measured, 2026-10-31)

The 100-case baseline was profiled before any expansion:

| Dimension | Base set | Gap |
|---|---|---|
| Publishers | 490/500 candidates from one publisher (postgresql.org) | "multi-source" meant multiple sections of one documentation site |
| Query shape | 5–20 words, single clause | no compound or context-dependent queries |
| Relevance | binary gold only | no partial relevance; distractors are random passages, never crafted near-ties (base-set lexical ranking is near ceiling: mean gold-vs-best-distractor gap 0.107, 27/100 accidental hard cases) |
| Coverage | 74 cases with 1 gold, 26 with 2 | no case needs 3+ evidence passages |

## Targeted extension set — Synthetic Extension Benchmark (51 cases, seed-pinned split 40/11)

**Classification: synthetic benchmark.** `scripts/build_extension_dataset.py`
builds 51 cases over a new original corpus
(`data/corpus/extension_passages.jsonl`, 64 authored passages across three
fictional publishers; see `data/corpus/NOTICE.md`). It controls difficulty and
publisher diversity by construction but does **not** evidence retrieval quality
on real-world documents; its results are always reported separately from the
PostgreSQL baseline, and a real-world document benchmark remains future work.
Extension gold passages are disjoint from the base set by construction; the
base set and its split are untouched. The extension splits with the same
component-disjoint algorithm under its own pinned seed (20261031), so its 11
held-out cases stay fixed.

### Hardness audit (measured, `scripts/audit_extension_hardness.py`)

The audit proves the crafted cases are hard rather than hard in name only,
under the frozen lexical scorer, and fails CI if the properties regress:

| Property | Threshold | Measured |
|---|---|---|
| Gold not top-ranked (flip rate) | [0.375, 0.75] — neither lexical-favored nor lexical-hostile | 0.625 (10/16) |
| Confuser query-term coverage vs gold | ≥ 0.8 | 0.993 |
| Median gold-vs-confuser score gap | ≤ 0.05 (half the base set's 0.107 mean gap) | 0.049 |
| Max gap (no runaway) | ≤ 0.15 | 0.120 |
| Partial (grade 1) outscores fillers | ≥ 8/10 cases | 9/10 |

Judgments were authored from corpus facts before any scorer ran on them; the
flip band above rejects construction tuned for or against either scorer.
Reproduce: `python scripts/audit_extension_hardness.py` (exit 1 on failure).

| Case type | n | Gap targeted | Lexical result |
|---|---|---|---|
| hard_negative | 16 | crafted confusers sharing query vocabulary with wrong values (legacy versions, other tiers/components) | P@1 = 0.500 (chance level), MAP = 0.750 |
| partial_relevance | 10 | graded relevance 0/1/2 with linear-gain nDCG | P@1 = 0.300 — lexical ranks partial answers above full ones; graded nDCG@5 = 0.831 vs binary 0.704 |
| coverage | 10 | 3 gold passages required | MAP = 0.838 |
| compound_query | 10 | multi-constraint questions | MAP = 0.928 |
| context_dependent | 5 | answer depends on the version named in the query | MAP = 1.000 |

Extension overall (lexical): MAP 0.798, P@1 0.667, R@5 1.000, nDCG@5 0.858,
MRR 0.821 — versus near-ceiling lexical results on the base set, so the
extension is where a JEV-vs-lexical difference can actually show.

**JEV on the extension: pending.** No real JEV measurements exist for the
extension cases, and none can be produced without opening provider egress,
which stays closed. The extension is measurement-ready: the moment an
authorized provider run is approved, the same script computes the paired
comparison with the same protocol and seed discipline.

## Held-out comparison (22 records, both scorers)

| Metric | Lexical | JEV | Δ (JEV − lexical) | 95% CI of Δ |
|---|---|---|---|---|
| MAP | 0.9735 | 0.9924 | +0.0189 | [−0.0152, +0.0644] |
| MRR | 0.9773 | 1.0000 | +0.0227 | [0.0000, +0.0682] |
| P@1 | 0.9545 | 1.0000 | +0.0455 | [0.0000, +0.1364] |
| R@1 | 0.8182 | 0.8409 | +0.0227 | [0.0000, +0.0682] |
| nDCG@1 | 0.9545 | 1.0000 | +0.0455 | [0.0000, +0.1364] |
| P@3 | 0.4394 | 0.4394 | 0.0000 | [0.0000, 0.0000] |
| R@3 | 1.0000 | 1.0000 | 0.0000 | [0.0000, 0.0000] |
| nDCG@3 | 0.9824 | 0.9964 | +0.0139 | [−0.0073, +0.0455] |
| P@5 | 0.2636 | 0.2636 | 0.0000 | [0.0000, 0.0000] |
| R@5 | 1.0000 | 1.0000 | 0.0000 | [0.0000, 0.0000] |
| nDCG@5 | 0.9824 | 0.9964 | +0.0139 | [−0.0073, +0.0455] |

## Multi-source breakdown (MAP)

Held-out 22 records, both scorers:

| Sources per case | n | Lexical | JEV |
|---|---|---|---|
| 1 | 6 | 0.9306 | 1.0000 |
| 2 | 11 | 0.9848 | 0.9848 |
| 3+ | 5 | 1.0000 | 1.0000 |

By case type (held-out):

| Case type | n | Lexical | JEV |
|---|---|---|---|
| multi_source | 7 | 0.9167 | 0.9762 |
| direct_factual | 11 | 1.0000 | 1.0000 |
| ambiguous | 2 | 1.0000 | 1.0000 |
| false_premise | 1 | 1.0000 | 1.0000 |
| prompt_injection | 1 | 1.0000 | 1.0000 |

Unified 100 cases, lexical only: 1 source 0.8571 (n=40), 2 sources 0.8111
(n=33), 3+ sources 0.9383 (n=27).

## Reading

* Both scorers saturate recall at k≥3 on this dataset; differentiation lives
  at rank 1 and in ranking quality (MAP/nDCG).
* The JEV advantage concentrates exactly where lexical is weakest: multi-source
  records (MAP 0.9167 → 0.9762) and the hardest single-source records
  (0.9306 → 1.0000). On easy record types the two are identical.
* No difference is decisive: every Δ confidence interval either includes zero
  (MAP, nDCG) or sits on the zero boundary (MRR, P@1, R@1, nDCG@1). With 22
  held-out records the evidence is suggestive but not conclusive — consistent
  with the frozen Phase 5B "Inconclusive" outcome.
* A decisive quality claim requires real JEV scores over a larger frozen set,
  which requires an authorized provider run. That decision is separate and
  remains closed; nothing in this phase opens egress or changes production
  settings.
