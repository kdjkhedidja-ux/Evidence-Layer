#!/usr/bin/env python3
"""Phase 6 quality evaluation: JEV vs lexical on the frozen unified dataset.

Deterministic and offline. No network access, no provider calls, no egress.

Design (fixed before running):

* **Unified evaluation set:** all 100 cases produced by
  ``build_dataset.build_cases()`` (78 dev + 22 held-out; membership pinned by
  the dataset splitter's fixed seed). Nothing is tuned on this data; the run
  is measurement only.
* **Lexical scores** are recomputed deterministically for every candidate of
  every case with the frozen default ``LexicalScorer``.
* **JEV scores** are the real, frozen per-candidate measurements from
  ``reports/phase5b_heldout_test_single_run.json`` — the only genuine JEV
  measurements in this repository. Live JEV transfer is hard-denied and no
  egress is opened, so the JEV comparison covers exactly the 22 held-out
  records; lexical-only metrics are also reported on the full unified set.
* **Metrics per record:** precision@k, recall@k, nDCG@k for k in {1, 3, 5},
  average precision (MAP over the full ranking), and MRR. Rankings sort by
  score descending with candidate_id ascending, matching the frozen selector
  tie behavior.
* **Uncertainty:** paired record-level percentile bootstrap (10,000
  replicates, linear interpolation, fixed seed) for every metric and for the
  paired JEV-minus-lexical difference on the held-out records.
* **Multi-source analysis:** metrics broken down by sources-per-case (1, 2,
  3+) and by case type, on both the unified set (lexical) and the held-out
  records (both scorers).

No superiority is predeclared for either scorer; the report states estimates
and confidence intervals only.

Writes ``reports/phase6_quality_evaluation.json`` (regenerable; reports/ is
gitignored and never committed).
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_dataset  # noqa: E402
from evidence_layer.metrics import (  # noqa: E402
    average_precision,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402

K_VALUES: tuple[int, ...] = (1, 3, 5)
FROZEN_JEV_REPORT = ROOT / "reports" / "phase5b_heldout_test_single_run.json"
OUTPUT_PATH = ROOT / "reports" / "phase6_quality_evaluation.json"
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20261031

MetricFn = Callable[[list[str], list[str]], float]


def _rank_candidate_ids(scores: Mapping[str, float]) -> list[str]:
    """Score descending, candidate_id ascending (frozen selector tie behavior)."""
    return [
        candidate_id
        for _, candidate_id in sorted(
            ((score, cid) for cid, score in scores.items()),
            key=lambda pair: (-pair[0], pair[1]),
        )
    ]


def _metric_functions(k: int) -> dict[str, MetricFn]:
    return {
        f"precision_at_{k}": lambda gold, ranked: precision_at_k(gold, ranked[:k]),
        f"recall_at_{k}": lambda gold, ranked: recall_at_k(gold, ranked[:k]),
        f"ndcg_at_{k}": lambda gold, ranked: ndcg_at_k(gold, ranked, k),
    }


def _record_metrics(gold_ids: list[str], ranked_ids: list[str]) -> dict[str, float]:
    values: dict[str, float] = {
        "average_precision": average_precision(gold_ids, ranked_ids),
        "mrr": mrr_at_k(gold_ids, ranked_ids),
    }
    for k in K_VALUES:
        values.update({name: fn(gold_ids, ranked_ids) for name, fn in _metric_functions(k).items()})
    return values


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _bootstrap_ci(
    paired: list[tuple[float, float]],
) -> dict[str, dict[str, float]]:
    """Paired record-level percentile bootstrap 95% CI for both arms and delta."""
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(paired)
    lexical_means: list[float] = []
    jev_means: list[float] = []
    deltas: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [paired[rng.randrange(n)] for _ in range(n)]
        lexical_mean = _mean(pair[0] for pair in sample)
        jev_mean = _mean(pair[1] for pair in sample)
        lexical_means.append(lexical_mean)
        jev_means.append(jev_mean)
        deltas.append(jev_mean - lexical_mean)

    def interval(samples: list[float]) -> dict[str, float]:
        samples = sorted(samples)
        lower = _percentile(samples, 2.5)
        upper = _percentile(samples, 97.5)
        return {"lower": lower, "upper": upper}

    return {
        "lexical": interval(lexical_means),
        "jev": interval(jev_means),
        "difference_jev_minus_lexical": interval(deltas),
    }


def _percentile(sorted_samples: list[float], percentile: float) -> float:
    """Linear-interpolation percentile, matching the frozen 5B methodology."""
    if not sorted_samples:
        return 0.0
    rank = (percentile / 100.0) * (len(sorted_samples) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_samples) - 1)
    fraction = rank - low
    return sorted_samples[low] + fraction * (sorted_samples[high] - sorted_samples[low])


def _source_bucket(case: dict) -> str:
    count = len({candidate["source_url"] for candidate in case["candidates"]})
    return str(count) if count < 3 else "3+"


def _breakdown(
    rows: list[dict], metric: str, key_fn: Callable[[dict], str]
) -> dict[str, dict[str, float]]:
    groups: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        bucket = groups.setdefault(key_fn(row), {"lexical": [], "jev": []})
        bucket["lexical"].append(row["lexical"][metric])
        if row["jev"] is not None:
            bucket["jev"].append(row["jev"][metric])
    return {
        name: {
            "records": len(values["lexical"]),
            "lexical": _mean(values["lexical"]),
            **(
                {"jev": _mean(values["jev"])}
                if values["jev"]
                else {}
            ),
        }
        for name, values in sorted(groups.items())
    }


def main() -> None:
    dev, test = build_dataset.build_cases()
    unified = dev + test
    held_out_ids = {case["case_id"] for case in test}

    frozen = json.loads(FROZEN_JEV_REPORT.read_text())
    jev_scores: dict[str, dict[str, float]] = {}
    for entry in frozen["per_candidate"]:
        jev_scores.setdefault(entry["record_id"], {})[entry["candidate_id"]] = float(
            entry["jev_score"]
        )
    unknown_records = set(jev_scores) - held_out_ids
    if unknown_records:
        raise SystemExit(f"Frozen JEV report has records outside the held-out split: {unknown_records}")
    if set(jev_scores) != held_out_ids:
        missing = held_out_ids - set(jev_scores)
        raise SystemExit(f"Frozen JEV report is missing held-out records: {sorted(missing)}")

    scorer = LexicalScorer()
    rows: list[dict] = []
    drift: list[str] = []
    for case in unified:
        case_id = case["case_id"]
        gold = list(case["gold_candidate_ids"])
        lexical_scores = {
            candidate["candidate_id"]: scorer.score(case["task"]["input"], candidate["text"])
            for candidate in case["candidates"]
        }
        lexical_ranked = _rank_candidate_ids(lexical_scores)
        lexical_metrics = _record_metrics(gold, lexical_ranked)

        jev_metrics = None
        if case_id in jev_scores:
            per_candidate = jev_scores[case_id]
            if set(per_candidate) != set(lexical_scores):
                raise SystemExit(f"Frozen JEV candidates diverge from the dataset for {case_id}")
            jev_ranked = _rank_candidate_ids(per_candidate)
            jev_metrics = _record_metrics(gold, jev_ranked)
            # Drift check: recomputed lexical scores must match the frozen run.
            for entry in frozen["per_candidate"]:
                if entry["record_id"] != case_id:
                    continue
                recomputed = lexical_scores[entry["candidate_id"]]
                if abs(recomputed - float(entry["lexical_score"])) > 1e-9:
                    drift.append(f"{case_id}/{entry['candidate_id']}")

        rows.append(
            {
                "case_id": case_id,
                "case_type": case["case_type"],
                "source_bucket": _source_bucket(case),
                "held_out": case_id in held_out_ids,
                "lexical": lexical_metrics,
                "jev": jev_metrics,
            }
        )

    if drift:
        raise SystemExit(
            "Lexical scorer drifted from the frozen run for: " + ", ".join(drift[:5])
        )

    metric_names = list(rows[0]["lexical"].keys())
    unified_summary = {
        name: _mean(row["lexical"][name] for row in rows) for name in metric_names
    }

    held_out_rows = [row for row in rows if row["held_out"]]
    held_out_summary: dict[str, dict[str, object]] = {}
    for name in metric_names:
        paired = [(row["lexical"][name], row["jev"][name]) for row in held_out_rows]
        held_out_summary[name] = {
            "point_estimates": {
                "lexical": _mean(pair[0] for pair in paired),
                "jev": _mean(pair[1] for pair in paired),
                "difference_jev_minus_lexical": _mean(pair[1] - pair[0] for pair in paired),
            },
            "paired_record_bootstrap_95_percent_ci": _bootstrap_ci(paired),
        }

    report = {
        "schema_version": 1,
        "phase": "6-quality-evaluation",
        "network": "none (offline; live JEV transfer remains hard-denied)",
        "evaluation_set": {
            "unified_cases": len(unified),
            "dev_cases": len(dev),
            "held_out_cases": len(test),
            "candidates_per_case": sorted({len(c["candidates"]) for c in unified}),
            "distinct_sources": len(
                {candidate["source_url"] for case in unified for candidate in case["candidates"]}
            ),
            "case_types": sorted({case["case_type"] for case in unified}),
        },
        "jev_scores": {
            "source": "reports/phase5b_heldout_test_single_run.json (frozen real measurements)",
            "records_covered": len(jev_scores),
            "coverage_note": (
                "Real JEV measurements exist only for the 22 held-out records. "
                "The JEV-vs-lexical comparison is restricted to those records; "
                "extending coverage requires an authorized provider run, which "
                "remains closed."
            ),
        },
        "k_values": list(K_VALUES),
        "bootstrap": {
            "method": "paired record-level percentile bootstrap",
            "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED,
            "percentile_interpolation": "linear",
        },
        "unified_lexical_summary": unified_summary,
        "held_out_comparison": held_out_summary,
        "multi_source_breakdown": {
            name: {
                "unified_lexical_by_source_count": _breakdown(
                    rows, name, lambda row: row["source_bucket"]
                ),
                "held_out_by_source_count": _breakdown(
                    held_out_rows, name, lambda row: row["source_bucket"]
                ),
                "held_out_by_case_type": _breakdown(
                    held_out_rows, name, lambda row: row["case_type"]
                ),
            }
            for name in ("average_precision", "mrr", "precision_at_5", "recall_at_5", "ndcg_at_5")
        },
        "decision_rule": (
            "No superiority predeclared. Estimates and confidence intervals are "
            "reported without a winner claim; a decisive JEV comparison on a "
            "larger set requires an authorized provider run."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(f"unified cases: {len(unified)} | held-out compared: {len(held_out_rows)}")
    try:
        print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")
    except ValueError:
        print(f"wrote {OUTPUT_PATH}")
    for name in metric_names:
        est = held_out_summary[name]["point_estimates"]
        ci = held_out_summary[name]["paired_record_bootstrap_95_percent_ci"][
            "difference_jev_minus_lexical"
        ]
        print(
            f"{name}: lexical={est['lexical']:.4f} jev={est['jev']:.4f} "
            f"delta={est['difference_jev_minus_lexical']:+.4f} "
            f"95% CI [{ci['lower']:+.4f}, {ci['upper']:+.4f}]"
        )


if __name__ == "__main__":
    main()
