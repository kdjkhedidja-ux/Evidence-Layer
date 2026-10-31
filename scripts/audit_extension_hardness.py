#!/usr/bin/env python3
"""Quantitative hardness audit for the Phase 6 synthetic extension benchmark.

Proves the crafted cases are genuinely hard rather than hard in name only, and
that the relevance judgments are content-driven rather than tuned for or
against either scorer. Fully offline and deterministic.

For every hard_negative case it measures, under the frozen lexical scorer:

* the gold passage's rank and score versus the best confuser's rank and score
  (a "flip" means a non-gold candidate outranks the gold),
* vocabulary overlap between the query and the gold versus the query and the
  best confuser (query-term coverage ratio), proving the confuser is close to
  the question's surface form,
* score-gap distribution, proving the margins are near-ties.

For partial_relevance cases it measures whether the grade-1 (partial)
candidates share more query vocabulary than the grade-0 fillers — evidence
that the grades track content rather than arbitrary labels.

Writes ``reports/phase6_extension_hardness_audit.json`` (regenerable,
gitignored) and prints a summary. Exit code is 1 when the audit properties
fail, so CI can gate on real hardness.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_extension_dataset as bx  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402

OUTPUT_PATH = ROOT / "reports" / "phase6_extension_hardness_audit.json"

# Hardness thresholds, calibrated against the base set's measured difficulty:
# the base set's mean gold-vs-best-distractor lexical gap is 0.107 with 27/100
# accidental hard cases. The extension's crafted near-ties must be clearly
# tighter: median |gap| at most half the base mean, and no runaway case.
MIN_FLIP_RATE = 0.375  # gold must NOT be top-ranked in at least ~4 of 16 cases
MAX_FLIP_RATE = 0.75  # but flips above ~12/16 would suggest anti-lexical rigging
MIN_CONFUSER_QUERY_COVERAGE = 0.8  # best confuser covers >= 80% of the gold's query-term coverage
MAX_MEDIAN_ABS_GAP = 0.05  # <= half the base set's mean gap (0.107)
MAX_ABS_GAP = 0.15  # no case may be a runaway in either direction


def _query_coverage(query_terms: Counter, text: str, scorer: LexicalScorer) -> float:
    if not query_terms:
        return 0.0
    text_terms = set(scorer.normalize(text))
    covered = sum(1 for term in query_terms if term in text_terms)
    return covered / len(query_terms)


def main() -> int:
    scorer = LexicalScorer()
    dev, test = bx.build_extension_cases()
    cases = dev + test

    hard_rows: list[dict] = []
    for case in cases:
        if case["case_type"] != "hard_negative":
            continue
        query = case["task"]["input"]
        query_terms = Counter(scorer.normalize(query))
        gold_id = case["gold_candidate_ids"][0]
        scores = {}
        coverage = {}
        for candidate in case["candidates"]:
            cid = candidate["candidate_id"]
            scores[cid] = scorer.score(query, candidate["text"])
            coverage[cid] = _query_coverage(query_terms, candidate["text"], scorer)
        ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))
        non_gold = [cid for cid in ranked if cid != gold_id]
        best_confuser = non_gold[0]
        gold_rank = ranked.index(gold_id) + 1
        hard_rows.append(
            {
                "case_id": case["case_id"],
                "gold_id": gold_id,
                "gold_rank": gold_rank,
                "gold_score": scores[gold_id],
                "best_confuser_id": best_confuser,
                "best_confuser_score": scores[best_confuser],
                "score_gap_gold_minus_confuser": scores[gold_id] - scores[best_confuser],
                "flipped": gold_rank > 1,
                "gold_query_coverage": coverage[gold_id],
                "confuser_query_coverage": coverage[best_confuser],
                "coverage_ratio_confuser_over_gold": (
                    coverage[best_confuser] / coverage[gold_id]
                    if coverage[gold_id] > 0
                    else 0.0
                ),
            }
        )

    flips = sum(1 for row in hard_rows if row["flipped"])
    flip_rate = flips / len(hard_rows)
    coverage_ratios = [row["coverage_ratio_confuser_over_gold"] for row in hard_rows]
    gaps = [row["score_gap_gold_minus_confuser"] for row in hard_rows]

    partial_rows: list[dict] = []
    for case in cases:
        if case["case_type"] != "partial_relevance":
            continue
        query = case["task"]["input"]
        grades = case["relevance_scores"]
        text_by_id = {c["candidate_id"]: c["text"] for c in case["candidates"]}
        partial_scores = [
            scorer.score(query, text_by_id[cid]) for cid, g in grades.items() if g == 1
        ]
        filler_scores = [
            scorer.score(query, c["text"])
            for c in case["candidates"]
            if c["candidate_id"] not in grades
        ]
        partial_rows.append(
            {
                "case_id": case["case_id"],
                "partial_mean_score": statistics.mean(partial_scores) if partial_scores else 0.0,
                "filler_mean_score": statistics.mean(filler_scores) if filler_scores else 0.0,
            }
        )
    partial_beats_fillers = sum(
        1 for row in partial_rows if row["partial_mean_score"] > row["filler_mean_score"]
    )

    checks = {
        "hard_negative_count": len(hard_rows) == 16,
        "flip_rate_in_near_tie_band": MIN_FLIP_RATE <= flip_rate <= MAX_FLIP_RATE,
        "confusers_cover_query_terms": (
            statistics.mean(coverage_ratios) >= MIN_CONFUSER_QUERY_COVERAGE
        ),
        "gaps_are_near_ties": statistics.median(abs(g) for g in gaps) <= MAX_MEDIAN_ABS_GAP
        and max(abs(g) for g in gaps) <= MAX_ABS_GAP,
        "partial_relevance_tracks_content": partial_beats_fillers >= 8,
    }

    report = {
        "schema_version": 1,
        "benchmark": "phase6 synthetic extension benchmark (fictional publishers; not real-world documents)",
        "method": "frozen lexical scorer; deterministic; no network",
        "thresholds": {
            "min_flip_rate": MIN_FLIP_RATE,
            "max_flip_rate": MAX_FLIP_RATE,
            "min_confuser_query_coverage": MIN_CONFUSER_QUERY_COVERAGE,
            "max_median_abs_gap": MAX_MEDIAN_ABS_GAP,
            "max_abs_gap": MAX_ABS_GAP,
            "calibration": "base-set mean gold-vs-distractor gap is 0.107",
        },
        "hard_negative": {
            "cases": len(hard_rows),
            "flips": flips,
            "flip_rate": flip_rate,
            "mean_abs_score_gap": statistics.mean(abs(g) for g in gaps),
            "median_abs_score_gap": statistics.median(abs(g) for g in gaps),
            "max_abs_score_gap": max(abs(g) for g in gaps),
            "mean_confuser_query_coverage_ratio": statistics.mean(coverage_ratios),
            "per_case": hard_rows,
        },
        "partial_relevance": {
            "cases": len(partial_rows),
            "partial_outscores_fillers_cases": partial_beats_fillers,
            "per_case": partial_rows,
        },
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "neutrality_note": (
            "Specs and grades were authored from corpus facts before any scorer "
            "ran on them; the audit's flip band [0.375, 0.75] rejects both "
            "lexical-favored and lexical-hostile construction."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    print(f"hard_negative: {len(hard_rows)} cases, flips {flips} (rate {flip_rate:.3f})")
    print(f"mean |gap| {report['hard_negative']['mean_abs_score_gap']:.4f}")
    print(
        "confuser query-coverage ratio "
        f"{report['hard_negative']['mean_confuser_query_coverage_ratio']:.3f}"
    )
    print(f"partial_relevance: partials outscore fillers in {partial_beats_fillers}/{len(partial_rows)} cases")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    try:
        print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")
    except ValueError:
        print(f"wrote {OUTPUT_PATH}")
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
