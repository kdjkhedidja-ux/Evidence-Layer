#!/usr/bin/env python3
"""Verify the saved Real-World v1 JEV results and metrics without network access."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_real_world_jev_evaluation import (  # noqa: E402
    EXPECTED_SPLIT_COUNTS,
    REPORT_PATH,
    MANIFEST_PATH,
    _canonical_bytes,
    _sha256,
    _verify_local_inputs,
    build_comparison_report,
)


def verify_saved_run() -> dict:
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

    expected_keys = set()
    cases = {
        (split, case["case_id"]): case
        for split, split_cases in cases_by_split.items()
        for case in split_cases
    }
    for split, split_cases in cases_by_split.items():
        for case in split_cases:
            for candidate in case["candidates"]:
                expected_keys.add((split, case["case_id"], candidate["candidate_id"]))
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
    for field in ("result_classification", "judgment_status", "held_out_status", "splits"):
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
