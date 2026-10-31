#!/usr/bin/env python3
"""Recompute deterministic, offline lexical metrics for Real-World Benchmark v1.

This evaluates developer-authored grades descriptively. It never calls JEV,
opens network access, or writes synthetic benchmark outputs.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_real_world_dataset as rw  # noqa: E402
from evidence_layer.metrics import (  # noqa: E402
    average_precision,
    graded_ndcg_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402

OUTPUT_PATH = ROOT / "reports" / "phase6_real_world_lexical.json"
K_VALUES = (1, 3, 5)


def _rank(scores: dict[str, float]) -> list[str]:
    return [cid for cid, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _evaluate(cases: list[dict], scorer: LexicalScorer) -> dict:
    rows = []
    for case in cases:
        grades = case["relevance_scores"]
        gold = case["gold_candidate_ids"]
        scores = {
            candidate["candidate_id"]: scorer.score(
                case["task"]["input"], candidate["text"]
            )
            for candidate in case["candidates"]
        }
        ranked = _rank(scores)
        metrics = {
            "average_precision": average_precision(gold, ranked),
            "mrr": mrr_at_k(gold, ranked),
        }
        for k in K_VALUES:
            metrics[f"precision_at_{k}"] = precision_at_k(gold, ranked[:k])
            metrics[f"recall_at_{k}"] = recall_at_k(gold, ranked[:k])
            metrics[f"ndcg_at_{k}"] = ndcg_at_k(gold, ranked, k)
        metrics["graded_ndcg_at_5"] = graded_ndcg_at_k(
            {candidate_id: float(grade) for candidate_id, grade in grades.items()},
            ranked,
            5,
        )
        rows.append({"case_id": case["case_id"], "case_type": case["case_type"], "metrics": metrics})

    metric_names = list(rows[0]["metrics"].keys()) if rows else []
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["case_type"]].append(row)
    return {
        "cases": len(rows),
        "lexical_summary": {
            name: _mean(row["metrics"][name] for row in rows)
            for name in metric_names
        },
        "lexical_by_case_type": {
            case_type: {
                "records": len(type_rows),
                **{
                    name: _mean(row["metrics"][name] for row in type_rows)
                    for name in metric_names
                },
            }
            for case_type, type_rows in sorted(by_type.items())
        },
        "per_case": rows,
    }


def build_report() -> dict:
    dev, validation, exposed_test = rw.build_real_world_cases()
    scorer = LexicalScorer()
    return {
        "schema_version": 1,
        "benchmark": "phase6-real-world-v1",
        "evaluation": "offline deterministic lexical baseline",
        "network": "none",
        "jev": "not run",
        "judgment_status": "developer-authored before scoring; carried forward after passage audit; independent blind review pending",
        "held_out_status": "exposed; not a blind test for future JEV comparison",
        "scorer": {
            "name": scorer.name,
            "implementation": "src/evidence_layer/scoring/lexical.py",
            "tie_break": "score descending, candidate_id ascending",
        },
        "split_seed": rw.REAL_WORLD_SPLIT_SEED,
        "dev": _evaluate(dev, scorer),
        "validation": _evaluate(validation, scorer),
        "exposed_test": _evaluate(exposed_test, scorer),
        "overall": _evaluate(dev + validation + exposed_test, scorer),
    }


def report_bytes(report: dict | None = None) -> bytes:
    return (json.dumps(report or build_report(), indent=2, ensure_ascii=False) + "\n").encode()


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(report_bytes())
    report = json.loads(OUTPUT_PATH.read_text())
    for split in ("dev", "validation", "exposed_test", "overall"):
        summary = report[split]["lexical_summary"]
        print(f"{split}: n={report[split]['cases']} MAP={summary['average_precision']:.3f} P@1={summary['precision_at_1']:.3f} R@5={summary['recall_at_5']:.3f} nDCG@5={summary['ndcg_at_5']:.3f} graded-nDCG@5={summary['graded_ndcg_at_5']:.3f} MRR={summary['mrr']:.3f}")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
