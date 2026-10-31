"""Guard the Phase 6 extension hardness audit's measured properties."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit_extension_hardness.py"


def _run_audit(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    import importlib.util

    spec = importlib.util.spec_from_file_location("audit_extension_hardness", AUDIT)
    module = importlib.util.module_from_spec(spec)
    out = tmp_path / "audit.json"
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUTPUT_PATH", out)
    assert module.main() == 0
    return json.loads(out.read_text())


def test_hardness_audit_passes_all_checks(tmp_path, monkeypatch):
    report = _run_audit(tmp_path, monkeypatch)
    assert report["all_checks_passed"]
    assert all(report["checks"].values())


def test_hard_negatives_are_measured_near_ties(tmp_path, monkeypatch):
    report = _run_audit(tmp_path, monkeypatch)
    hard = report["hard_negative"]
    assert hard["cases"] == 16
    # Gold is not always the top lexical match, but the construction is not
    # hostile either: the flip rate sits inside the predeclared band.
    assert 0.375 <= hard["flip_rate"] <= 0.75
    # Confusers cover the query's content terms at least as well as golds.
    assert hard["mean_confuser_query_coverage_ratio"] >= 0.8
    # Median margin is at most half the base set's mean gap (0.107).
    assert hard["median_abs_score_gap"] <= 0.05


def test_partial_relevance_grades_track_content(tmp_path, monkeypatch):
    report = _run_audit(tmp_path, monkeypatch)
    partial = report["partial_relevance"]
    assert partial["cases"] == 10
    # Grade-1 partials must share more query vocabulary than grade-0 fillers
    # in nearly all cases — the grades reflect content, not arbitrary labels.
    assert partial["partial_outscores_fillers_cases"] >= 8


def test_audit_report_labels_benchmark_as_synthetic(tmp_path, monkeypatch):
    report = _run_audit(tmp_path, monkeypatch)
    assert "synthetic" in report["benchmark"].lower()
    assert "fictional" in report["benchmark"].lower()


def test_audit_cli_is_deterministic(tmp_path):
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    first = subprocess.run(
        [sys.executable, str(AUDIT)], capture_output=True, text=True, env=env
    )
    second = subprocess.run(
        [sys.executable, str(AUDIT)], capture_output=True, text=True, env=env
    )
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout
