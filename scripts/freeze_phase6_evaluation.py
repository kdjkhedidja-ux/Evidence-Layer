#!/usr/bin/env python3
"""Freeze the Phase 6 evaluation state into an immutable, verifiable manifest.

Writes ``data/phase6_freeze_manifest.json`` (tracked) containing:

* SHA-256 of every input artifact (base corpus, extension corpus, builder
  scripts, evaluation script, audit script, frozen JEV report),
* the split seeds and pinned held-out memberships for both sets,
* SHA-256 of the regenerated dev/test splits and evaluation/audit reports,
* the benchmark classification (baseline PostgreSQL docs vs synthetic
  extension benchmark),
* the git commit the freeze was taken on.

``--verify`` regenerates everything deterministically and compares hashes,
proving the frozen state is reproducible and unmodified. Exit 1 on any drift.
No network access; no baseline artifact is ever written by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

MANIFEST_PATH = ROOT / "data" / "phase6_freeze_manifest.json"

INPUT_FILES = [
    "data/corpus/postgresql_passages.jsonl",
    "data/corpus/extension_passages.jsonl",
    "data/corpus/NOTICE.md",
    "scripts/build_dataset.py",
    "scripts/build_extension_dataset.py",
    "scripts/run_quality_evaluation.py",
    "scripts/audit_extension_hardness.py",
    "src/evidence_layer/metrics.py",
    "src/evidence_layer/schemas.py",
    "reports/phase5b_heldout_test_single_run.json",
]

GENERATED_FILES = [
    "data/dev/cases.jsonl",
    "data/test/cases.jsonl",
    "data/extension_dev/cases.jsonl",
    "data/extension_test/cases.jsonl",
    "reports/phase6_quality_evaluation.json",
    "reports/phase6_extension_hardness_audit.json",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _held_out_membership() -> dict[str, list[str]]:
    import build_dataset
    import build_extension_dataset

    _, base_test = build_dataset.build_cases()
    _, ext_test = build_extension_dataset.build_extension_cases()
    return {
        "baseline": sorted(c["case_id"] for c in base_test),
        "synthetic_extension": sorted(c["case_id"] for c in ext_test),
    }


def _regenerate() -> None:
    """Rebuild all generated artifacts deterministically, in place."""
    import build_dataset
    import build_extension_dataset

    build_dataset.main()
    build_extension_dataset.main()
    import run_quality_evaluation
    import audit_extension_hardness

    run_quality_evaluation.OUTPUT_PATH = (
        ROOT / "reports" / "phase6_quality_evaluation.json"
    )
    run_quality_evaluation.main()
    audit_extension_hardness.OUTPUT_PATH = (
        ROOT / "reports" / "phase6_extension_hardness_audit.json"
    )
    if audit_extension_hardness.main() != 0:
        raise SystemExit("hardness audit failed during freeze regeneration")


def build_manifest() -> dict:
    import build_dataset
    import build_extension_dataset

    return {
        "schema_version": 1,
        "phase": "6-quality-evaluation-freeze",
        "frozen_at_commit": _git_commit(),
        "benchmarks": {
            "baseline": {
                "kind": "PostgreSQL documentation corpus (real documents; PostgreSQL License)",
                "cases": 100,
                "split_seed": build_dataset.SPLIT_SEED,
                "dev_fraction": build_dataset.DEV_FRACTION,
            },
            "synthetic_extension": {
                "kind": (
                    "Synthetic extension benchmark: original authored passages, "
                    "fictional publishers; controls difficulty and publisher "
                    "diversity but does not evidence real-world retrieval quality"
                ),
                "cases": 51,
                "split_seed": build_extension_dataset.EXTENSION_SPLIT_SEED,
                "dev_fraction": build_extension_dataset.DEV_FRACTION,
            },
        },
        "input_sha256": {name: _sha256(ROOT / name) for name in INPUT_FILES},
        "generated_sha256": {name: _sha256(ROOT / name) for name in GENERATED_FILES},
        "held_out_membership": _held_out_membership(),
        "metrics_protocol": {
            "k_values": [1, 3, 5],
            "metrics": [
                "precision_at_k",
                "recall_at_k",
                "ndcg_at_k",
                "average_precision",
                "mrr",
                "graded_ndcg_at_k (partial_relevance cases)",
            ],
            "bootstrap": "paired record-level percentile, 10000 replicates, seed 20261031, linear interpolation",
            "tie_break": "score descending, candidate_id ascending",
        },
        "hardness_audit_thresholds": {
            "flip_rate_band": [0.375, 0.75],
            "min_confuser_query_coverage": 0.8,
            "max_median_abs_gap": 0.05,
            "max_abs_gap": 0.15,
            "calibration": "base-set mean gold-vs-distractor gap 0.107",
        },
        "notes": (
            "Baseline and synthetic extension are always reported separately. "
            "JEV coverage of the extension is pending an authorized provider "
            "run; live transfer remains hard-denied. Real-world benchmark is a "
            "separate later phase."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="regenerate deterministically and compare against the frozen manifest",
    )
    args = parser.parse_args()

    if not args.verify:
        _regenerate()
        manifest = build_manifest()
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"froze Phase 6 evaluation state -> {MANIFEST_PATH.relative_to(ROOT)}")
        return 0

    if not MANIFEST_PATH.exists():
        print("freeze manifest is missing; run without --verify first", file=sys.stderr)
        return 1
    frozen = json.loads(MANIFEST_PATH.read_text())
    before_inputs = {name: _sha256(ROOT / name) for name in INPUT_FILES}
    if before_inputs != frozen["input_sha256"]:
        changed = [k for k in before_inputs if before_inputs[k] != frozen["input_sha256"][k]]
        print(f"INPUT DRIFT: {changed}", file=sys.stderr)
        return 1

    _regenerate()
    regenerated = build_manifest()
    for section in ("generated_sha256", "held_out_membership"):
        if regenerated[section] != frozen[section]:
            print(f"REGENERATION DRIFT in {section}", file=sys.stderr)
            return 1
    print("Phase 6 frozen state verified: inputs unchanged, regeneration identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
