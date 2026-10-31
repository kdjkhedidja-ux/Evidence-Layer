"""Guard the frozen Phase 6 evaluation state against silent drift."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "phase6_freeze_manifest.json"
FREEZE = ROOT / "scripts" / "freeze_phase6_evaluation.py"


def test_freeze_manifest_exists_and_is_complete():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["benchmarks"]["baseline"]["cases"] == 100
    assert manifest["benchmarks"]["baseline"]["split_seed"] == 2
    assert manifest["benchmarks"]["synthetic_extension"]["cases"] == 51
    assert manifest["benchmarks"]["synthetic_extension"]["split_seed"] == 20261031
    assert "synthetic" in manifest["benchmarks"]["synthetic_extension"]["kind"].lower()
    assert len(manifest["held_out_membership"]["baseline"]) == 22
    assert len(manifest["held_out_membership"]["synthetic_extension"]) == 11
    assert set(manifest["held_out_membership"]["baseline"]).isdisjoint(
        manifest["held_out_membership"]["synthetic_extension"]
    )
    # Every hashed input must exist and match its frozen digest.
    for name, digest in manifest["input_sha256"].items():
        path = ROOT / name
        assert path.exists(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_frozen_state_reproduces_byte_identically():
    result = subprocess.run(
        [sys.executable, str(FREEZE), "--verify"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr[-500:]


def test_baseline_results_unchanged_by_extension_work():
    # The frozen baseline comparison must stay at the Phase 5B values.
    report = json.loads(
        (ROOT / "reports" / "phase6_quality_evaluation.json").read_text()
    )
    estimates = report["held_out_comparison"]["average_precision"]["point_estimates"]
    assert estimates["lexical"] == pytest.approx(0.9734848484848485)
    assert estimates["jev"] == pytest.approx(0.9924242424242424)
