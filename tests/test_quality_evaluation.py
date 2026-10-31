"""Tests for the Phase 6 quality-evaluation script helpers and run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_quality_evaluation as qe  # noqa: E402


def test_rank_candidate_ids_score_desc_then_id_asc():
    scores = {"b": 0.5, "a": 0.5, "c": 0.9, "d": 0.1}
    assert qe._rank_candidate_ids(scores) == ["c", "a", "b", "d"]


def test_record_metrics_cover_full_suite():
    gold = ["g1", "g2"]
    ranked = ["g1", "x", "g2", "y", "z"]
    values = qe._record_metrics(gold, ranked)
    expected_keys = {
        "average_precision",
        "mrr",
        "precision_at_1",
        "recall_at_1",
        "ndcg_at_1",
        "precision_at_3",
        "recall_at_3",
        "ndcg_at_3",
        "precision_at_5",
        "recall_at_5",
        "ndcg_at_5",
    }
    assert set(values) == expected_keys
    assert values["precision_at_1"] == 1.0
    assert values["recall_at_5"] == 1.0
    assert values["average_precision"] == pytest.approx((1.0 + 2 / 3) / 2)


def test_percentile_linear_interpolation():
    samples = [0.0, 1.0, 2.0, 3.0]
    assert qe._percentile(samples, 50) == pytest.approx(1.5)
    assert qe._percentile(samples, 0) == 0.0
    assert qe._percentile(samples, 100) == 3.0
    assert qe._percentile([], 50) == 0.0


def test_bootstrap_ci_is_deterministic_and_paired():
    paired = [(0.8, 0.9), (1.0, 1.0), (0.6, 0.7), (0.9, 0.95)]
    first = qe._bootstrap_ci(paired)
    second = qe._bootstrap_ci(paired)
    assert first == second
    delta = first["difference_jev_minus_lexical"]
    assert delta["lower"] <= delta["upper"]
    # Point delta is positive everywhere, so the CI lower bound cannot be
    # below zero by more than resampling allows; with this tiny all-positive
    # sample it stays non-negative.
    assert delta["lower"] >= 0.0


def test_source_bucket():
    case = {
        "candidates": [
            {"source_url": "https://a.test"},
            {"source_url": "https://a.test"},
            {"source_url": "https://b.test"},
        ]
    }
    assert qe._source_bucket(case) == "2"
    case["candidates"].append({"source_url": "https://c.test"})
    assert qe._source_bucket(case) == "3+"


def test_breakdown_separates_jev_coverage():
    rows = [
        {
            "case_type": "direct_factual",
            "source_bucket": "1",
            "lexical": {"average_precision": 1.0},
            "jev": {"average_precision": 1.0},
        },
        {
            "case_type": "direct_factual",
            "source_bucket": "1",
            "lexical": {"average_precision": 0.5},
            "jev": None,
        },
    ]
    result = qe._breakdown(rows, "average_precision", lambda row: row["source_bucket"])
    assert result["1"]["records"] == 2
    assert result["1"]["lexical"] == pytest.approx(0.75)
    assert result["1"]["jev"] == 1.0


def test_main_runs_offline_and_writes_report(tmp_path, monkeypatch):
    output = tmp_path / "phase6_quality_evaluation.json"
    monkeypatch.setattr(qe, "OUTPUT_PATH", output)
    qe.main()

    report = json.loads(output.read_text())
    assert report["baseline_set"]["unified_cases"] == 100
    assert report["baseline_set"]["held_out_cases"] == 22
    extension = report["extension_set"]
    assert extension["cases"] == 51
    assert extension["held_out_cases"] == 11
    assert extension["publishers"] == ["cloudtier/4", "opsrunbook/2026", "streamspec/2.1"]
    assert "pending" in extension["jev_status"]
    # The extension's crafted hard negatives must be measurably harder for the
    # lexical baseline than the base set's near-ceiling ranking.
    hard = extension["lexical_by_case_type"]["hard_negative"]["lexical"]
    assert hard["precision_at_1"] < 1.0
    partial = extension["lexical_by_case_type"]["partial_relevance"]["lexical"]
    assert partial["graded_ndcg_at_5"] > 0.0
    comparison = report["held_out_comparison"]
    assert set(comparison) >= {
        "average_precision",
        "mrr",
        "precision_at_5",
        "recall_at_5",
        "ndcg_at_5",
    }
    # The frozen JEV coverage note must be explicit about the 22-record limit.
    assert report["jev_scores"]["records_covered"] == 22
    assert "egress" in report["decision_rule"] or "authorized provider run" in report["decision_rule"]
    # Determinism: a second run produces an identical report.
    qe.main()
    assert json.loads(output.read_text()) == report
