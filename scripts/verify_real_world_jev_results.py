#!/usr/bin/env python3
"""Verify the saved Real-World v1 JEV results and metrics without network access."""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_real_world_jev_evaluation import (  # noqa: E402
    EXPECTED_SPLIT_COUNTS,
    EXPECTED_METRIC_NAMES,
    REPORT_PATH,
    MANIFEST_PATH,
    _canonical_bytes,
    _sha256,
    _verify_local_inputs,
    build_comparison_report,
)
from run_real_world_evaluation import _evaluate  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402

BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_BASE_SEED = 20261031
BOOTSTRAP_SPLIT_SEEDS = {
    "dev": BOOTSTRAP_BASE_SEED,
    "validation": BOOTSTRAP_BASE_SEED + 1,
    "exposed_test": BOOTSTRAP_BASE_SEED + 2,
    "overall": BOOTSTRAP_BASE_SEED + 3,
}
BOOTSTRAP_CONFIDENCE_LEVEL = 0.95


def _ensure_generated_splits() -> None:
    split_paths = [
        ROOT / "data/real_world_dev/cases.jsonl",
        ROOT / "data/real_world_validation/cases.jsonl",
        ROOT / "data/real_world_test/cases.jsonl",
    ]
    missing = [path for path in split_paths if not path.is_file()]
    if not missing:
        return
    if len(missing) != len(split_paths):
        raise ValueError(
            "generated split files are incomplete; review them and regenerate with "
            "scripts/build_real_world_dataset.py"
        )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_real_world_dataset.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or any(not path.is_file() for path in split_paths):
        raise ValueError("could not materialize the ignored Real-World v1 split files")


def _expected_candidate_keys(cases_by_split: dict[str, list[dict]]) -> set[tuple[str, str, str]]:
    keys = set()
    for split, split_cases in cases_by_split.items():
        for case in split_cases:
            candidate_ids = [candidate["candidate_id"] for candidate in case["candidates"]]
            if len(candidate_ids) != 5 or len(set(candidate_ids)) != 5:
                raise ValueError("each fixed benchmark case must contain five unique candidates")
            keys.update((split, case["case_id"], candidate_id) for candidate_id in candidate_ids)
    return keys


def _assert_same_case_candidate_universe(
    cases_by_split: dict[str, list[dict]],
    candidate_outputs: list[dict],
    lexical_report: dict,
) -> None:
    """Require both systems' metrics to cover the same fixed case/candidate universe."""
    expected_keys = _expected_candidate_keys(cases_by_split)
    actual_keys = [
        (row.get("split"), row.get("case_id"), row.get("candidate_id"))
        for row in candidate_outputs
    ]
    if len(actual_keys) != len(set(actual_keys)):
        raise ValueError("candidate output is duplicated")
    if set(actual_keys) != expected_keys:
        raise ValueError("JEV and Lexical must use the same fixed 40 cases and 200 candidates")

    for split, cases in cases_by_split.items():
        expected_case_ids = [case["case_id"] for case in cases]
        lexical_case_ids = [row.get("case_id") for row in lexical_report[split]["per_case"]]
        if lexical_case_ids != expected_case_ids:
            raise ValueError("Lexical case metrics do not cover the fixed cases in order")
    expected_overall_case_ids = [
        case["case_id"]
        for split in ("dev", "validation", "exposed_test")
        for case in cases_by_split[split]
    ]
    if [row.get("case_id") for row in lexical_report["overall"]["per_case"]] != expected_overall_case_ids:
        raise ValueError("overall Lexical metrics do not cover the fixed 40 cases in order")


def _percentile(sorted_values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile (the common type-7 definition)."""
    if not sorted_values:
        raise ValueError("cannot calculate a percentile from an empty sample")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        value = sorted_values[lower]
    else:
        fraction = position - lower
        value = sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction
    return 0.0 if abs(value) < 1e-14 else value


def _paired_case_bootstrap(
    cases_by_split: dict[str, list[dict]],
    reproduced_splits: dict[str, dict],
    lexical_report: dict,
) -> dict:
    """Bootstrap paired JEV-minus-Lexical per-case metrics, never candidate rows."""
    analysis = {}
    for split, seed in BOOTSTRAP_SPLIT_SEEDS.items():
        jev_rows = reproduced_splits[split]["per_case"]
        lexical_rows = lexical_report[split]["per_case"]
        case_ids = [case["case_id"] for case in cases_by_split[split]] if split != "overall" else [
            case["case_id"]
            for name in ("dev", "validation", "exposed_test")
            for case in cases_by_split[name]
        ]
        if [row["case_id"] for row in jev_rows] != case_ids or [
            row["case_id"] for row in lexical_rows
        ] != case_ids:
            raise ValueError(f"{split} bootstrap rows do not align by case_id")
        if any(not row.get("complete") or row.get("metrics") is None for row in jev_rows):
            raise ValueError(f"{split} bootstrap requires complete JEV metrics for every case")

        paired_differences = {
            metric: [
                jev_row["metrics"][metric] - lexical_row["metrics"][metric]
                for jev_row, lexical_row in zip(jev_rows, lexical_rows, strict=True)
            ]
            for metric in EXPECTED_METRIC_NAMES
        }
        case_count = len(case_ids)
        if case_count == 0:
            raise ValueError(f"{split} bootstrap has no cases")
        point_estimates = {
            metric: sum(differences) / case_count
            for metric, differences in paired_differences.items()
        }
        point_estimates = {
            metric: 0.0 if abs(value) < 1e-14 else value
            for metric, value in point_estimates.items()
        }
        rng = random.Random(seed)
        replicates = {metric: [] for metric in EXPECTED_METRIC_NAMES}
        for _ in range(BOOTSTRAP_REPLICATES):
            sampled_case_indices = [rng.randrange(case_count) for _ in range(case_count)]
            for metric, differences in paired_differences.items():
                replicates[metric].append(
                    sum(differences[index] for index in sampled_case_indices) / case_count
                )
        metric_intervals = {}
        for metric in EXPECTED_METRIC_NAMES:
            expected_delta = reproduced_splits[split]["comparison"][metric][
                "delta_jev_minus_lexical"
            ]
            if not math.isclose(point_estimates[metric], expected_delta, abs_tol=1e-12):
                raise ValueError(f"{split} {metric} paired estimate differs from the metric delta")
            sorted_replicates = sorted(replicates[metric])
            metric_intervals[metric] = {
                "point_estimate": point_estimates[metric],
                "ci_lower": _percentile(sorted_replicates, 0.025),
                "ci_upper": _percentile(sorted_replicates, 0.975),
            }
        analysis[split] = {
            "cases": case_count,
            "seed": seed,
            "metrics": metric_intervals,
        }
    return {
        "method": "paired percentile bootstrap of per-case JEV-minus-Lexical metric differences",
        "resampling_unit": "case_id; each sampled unit preserves the JEV/Lexical pair",
        "replicates": BOOTSTRAP_REPLICATES,
        "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
        "percentile_definition": "linear interpolation at (n - 1) * p",
        "base_seed": BOOTSTRAP_BASE_SEED,
        "splits": analysis,
        "interpretation": (
            "descriptive exploratory intervals on developer-authored judgments; "
            "not confirmatory evidence or a blind evaluation"
        ),
    }


def verify_saved_run() -> dict:
    _ensure_generated_splits()
    if not REPORT_PATH.is_file() or not MANIFEST_PATH.is_file():
        raise ValueError("saved JEV report or manifest is missing")
    report_bytes = REPORT_PATH.read_bytes()
    report = json.loads(report_bytes)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if _sha256(report_bytes) != manifest.get("results_sha256"):
        raise ValueError("saved JEV report SHA-256 does not match its manifest")

    candidate_outputs = report.get("candidate_outputs")
    if not isinstance(candidate_outputs, list):
        raise ValueError("saved candidate outputs are missing")
    outputs_sha256 = _sha256(_canonical_bytes(candidate_outputs))
    if outputs_sha256 != manifest.get("raw_score_outputs_sha256"):
        raise ValueError("raw score outputs SHA-256 does not match the manifest")
    if report.get("run", {}).get("outputs_sha256") != outputs_sha256:
        raise ValueError("report output digest is inconsistent")

    cases_by_split, input_hashes, metadata = _verify_local_inputs()
    if input_hashes != manifest.get("input_sha256"):
        raise ValueError("one or more benchmark, code, or freeze inputs changed")
    if report["run"].get("input_sha256") != input_hashes:
        raise ValueError("run input fingerprints differ from the verification manifest")
    if metadata["generated_cases_sha256"] != manifest.get("generated_cases_sha256"):
        raise ValueError("generated Real-World v1 cases differ from the run manifest")
    if metadata["freeze_manifest_sha256"] != manifest.get(
        "synthetic_freeze_manifest_sha256"
    ):
        raise ValueError("the synthetic freeze manifest differs from the run manifest")
    if metadata["synthetic_result_sha256"] != manifest.get(
        "synthetic_result_sha256"
    ):
        raise ValueError("the frozen synthetic evaluation result differs")

    analysis_manifest = manifest.get("offline_analysis", {})
    analysis_script = ROOT / "scripts/verify_real_world_jev_results.py"
    if _sha256(analysis_script.read_bytes()) != analysis_manifest.get("script_sha256"):
        raise ValueError("offline analysis script SHA-256 does not match its manifest")

    scope = {
        "run_id": manifest["run_id"],
        "input_sha256": input_hashes,
        "generated_cases_sha256": metadata["generated_cases_sha256"],
        "model": report["run"]["model_requested"],
        "prompt_sha256": report["run"]["prompt_sha256"],
        "sdk_version": report["run"]["sdk_version"],
        "request_limit": 200,
    }
    if _sha256(_canonical_bytes(scope)) != manifest.get("run_scope_sha256"):
        raise ValueError("run scope SHA-256 does not match its manifest")

    cases = {
        (split, case["case_id"]): case
        for split, split_cases in cases_by_split.items()
        for case in split_cases
    }
    expected_keys = _expected_candidate_keys(cases_by_split)
    actual_keys = set()
    for row in candidate_outputs:
        key = (row.get("split"), row.get("case_id"), row.get("candidate_id"))
        if key in actual_keys:
            raise ValueError("candidate output is duplicated")
        actual_keys.add(key)
        case = cases.get((key[0], key[1]))
        candidate = next(
            (
                item
                for item in case["candidates"]
                if item["candidate_id"] == key[2]
            ),
            None,
        ) if case else None
        if candidate is None:
            raise ValueError("candidate output is outside the fixed benchmark")
        query_sha256 = _sha256(case["task"]["input"].encode("utf-8"))
        text_sha256 = _sha256(candidate["text"].encode("utf-8"))
        if row.get("query_sha256") != query_sha256 or row.get(
            "candidate_text_sha256"
        ) != text_sha256:
            raise ValueError("saved output is bound to different query/candidate text")
        score = row.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError("saved JEV score is missing or outside [0, 1]")
    if actual_keys != expected_keys:
        raise ValueError("saved outputs do not cover all 200 fixed candidates")
    _assert_same_case_candidate_universe(
        cases_by_split, candidate_outputs, metadata["lexical_report"]
    )

    # Recompute every Lexical per-case result from the same fixed candidate records.
    lexical_scorer = LexicalScorer()
    lexical_cases = {
        "dev": cases_by_split["dev"],
        "validation": cases_by_split["validation"],
        "exposed_test": cases_by_split["exposed_test"],
        "overall": (
            cases_by_split["dev"]
            + cases_by_split["validation"]
            + cases_by_split["exposed_test"]
        ),
    }
    for split, split_cases in lexical_cases.items():
        recomputed_lexical = _evaluate(split_cases, lexical_scorer)
        if recomputed_lexical["per_case"] != metadata["lexical_report"][split]["per_case"]:
            raise ValueError(f"saved {split} Lexical per-case metrics do not reproduce")

    counts = report["run"]["counts"]
    expected_counts = {
        "expected_cases": 40,
        "expected_candidates": 200,
        "sdk_calls": 200,
        "http_request_events": 200,
        "http_responses": 200,
        "http_status_counts": {"200": 200},
        "successful_scores": 200,
        "failed_scores": 0,
        "unused_authorized_calls": 0,
    }
    if counts != expected_counts:
        raise ValueError("saved run did not complete the full 200-request evaluation")
    if report["run"]["inputs_unchanged_after_run"] is not True:
        raise ValueError("input preservation was not verified after the live run")
    if report["result_classification"] != "exploratory; not independent blind evidence":
        raise ValueError("the result classification does not preserve the review limits")

    reproduced = build_comparison_report(
        cases_by_split,
        metadata["lexical_report"],
        candidate_outputs,
    )
    reproduced["paired_bootstrap"] = _paired_case_bootstrap(
        cases_by_split, reproduced["splits"], metadata["lexical_report"]
    )
    for field in (
        "result_classification",
        "judgment_status",
        "held_out_status",
        "splits",
        "paired_bootstrap",
    ):
        if reproduced[field] != report[field]:
            raise ValueError(f"saved {field} differs from reproduced metrics")
    if {split: len(rows) for split, rows in cases_by_split.items()} != EXPECTED_SPLIT_COUNTS:
        raise ValueError("Real-World v1 split counts differ")
    return report


def main() -> int:
    try:
        report = verify_saved_run()
    except Exception as error:
        print(f"JEV result verification failed: {error}", file=sys.stderr)
        return 1
    print("Real-World v1 JEV result verification passed (offline).")
    for split, values in report["splits"].items():
        metric = values["comparison"]["average_precision"]
        print(
            f"{split}: MAP JEV={metric['jev']:.3f} "
            f"Lexical={metric['lexical']:.3f} "
            f"delta={metric['delta_jev_minus_lexical']:+.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
