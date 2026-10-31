"""Offline reproducibility checks for the real-world and frozen synthetic sets."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_quality_evaluation as phase6  # noqa: E402
import verify_real_world_benchmark as real_world_verifier  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402
from verify_real_world_benchmark import verify  # noqa: E402

EXPECTED_SYNTHETIC_FREEZE_SHA256 = "0082546fed1b623a89ab032fd39b5d9aca413817867d34690a1babaa51244f31"


def test_real_world_manifest_and_lexical_report_reproduce_offline(tmp_path, monkeypatch):
    splits = real_world_verifier.rw.build_real_world_cases()
    split_outputs = {
        "data/real_world_dev/cases.jsonl": tmp_path / "dev.jsonl",
        "data/real_world_validation/cases.jsonl": tmp_path / "validation.jsonl",
        "data/real_world_test/cases.jsonl": tmp_path / "test.jsonl",
    }
    for (_, path), rows in zip(split_outputs.items(), splits, strict=True):
        path.write_bytes(real_world_verifier._split_bytes(rows))
    monkeypatch.setattr(real_world_verifier, "SPLIT_OUTPUTS", split_outputs)
    assert verify() == []

    path = split_outputs["data/real_world_dev/cases.jsonl"]
    path.write_bytes(path.read_bytes() + b"tamper\n")
    assert "generated split mismatch: data/real_world_dev/cases.jsonl" in verify()


def test_synthetic_freeze_manifest_and_extension_metrics_are_unchanged():
    freeze_path = ROOT / "data" / "phase6_freeze_manifest.json"
    assert hashlib.sha256(freeze_path.read_bytes()).hexdigest() == EXPECTED_SYNTHETIC_FREEZE_SHA256

    freeze = json.loads(freeze_path.read_text())
    for relative_path, expected in freeze["input_sha256"].items():
        path = ROOT / relative_path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative_path
    baseline_dev, baseline_test = phase6.build_dataset.build_cases()
    extension_dev, extension_test = phase6.build_extension_dataset.build_extension_cases()
    generated_splits = {
        "data/dev/cases.jsonl": baseline_dev,
        "data/test/cases.jsonl": baseline_test,
        "data/extension_dev/cases.jsonl": extension_dev,
        "data/extension_test/cases.jsonl": extension_test,
    }
    for relative_path, rows in generated_splits.items():
        data = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
        assert hashlib.sha256(data).hexdigest() == freeze["generated_sha256"][relative_path]

    synthetic = phase6._evaluate_extension(LexicalScorer())
    assert synthetic["cases"] == 51
    assert synthetic["dev_cases"] == 40
    assert synthetic["held_out_cases"] == 11
    assert synthetic["lexical_summary"]["average_precision"] == pytest.approx(0.7783224400871459)
    assert synthetic["lexical_summary"]["ndcg_at_5"] == pytest.approx(0.8435788422934303)
    assert synthetic["lexical_summary"]["graded_ndcg_at_5"] == pytest.approx(0.8309855171334117)

    scorer = LexicalScorer()
    baseline_ap = []
    for case in baseline_test:
        scores = {
            candidate["candidate_id"]: scorer.score(
                case["task"]["input"], candidate["text"]
            )
            for candidate in case["candidates"]
        }
        ranked = phase6._rank_candidate_ids(scores)
        baseline_ap.append(
            phase6._record_metrics(case["gold_candidate_ids"], ranked)["average_precision"]
        )
    baseline_ap = sum(baseline_ap) / len(baseline_ap)
    assert baseline_ap == pytest.approx(0.9734848484848485)
