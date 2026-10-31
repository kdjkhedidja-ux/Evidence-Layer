from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_real_world_jev_evaluation as evaluation
from scripts.verify_real_world_jev_results import (
    _assert_same_case_candidate_universe,
    _paired_case_bootstrap,
    verify_saved_run,
)


@pytest.fixture(scope="module", autouse=True)
def materialize_ignored_real_world_splits():
    split_paths = [
        Path("data/real_world_dev/cases.jsonl"),
        Path("data/real_world_validation/cases.jsonl"),
        Path("data/real_world_test/cases.jsonl"),
    ]
    missing = [path for path in split_paths if not path.is_file()]
    if len(missing) == len(split_paths):
        import build_real_world_dataset as rw

        rw.main()
    elif missing:
        pytest.fail("generated Real-World v1 split files are incomplete")


@pytest.fixture(scope="module")
def fixed_cases():
    import build_real_world_dataset as rw

    dev, validation, exposed_test = rw.build_real_world_cases()
    return {"dev": dev, "validation": validation, "exposed_test": exposed_test}


def _outputs(cases_by_split):
    records = []
    for split, cases in cases_by_split.items():
        for case in cases:
            for index, candidate in enumerate(case["candidates"]):
                records.append(
                    {
                        "split": split,
                        "case_id": case["case_id"],
                        "candidate_id": candidate["candidate_id"],
                        "score": float(5 - index),
                    }
                )
    return records


def test_local_verification_recomputes_lexical_baseline_and_preserves_synthetic_hash():
    cases, input_hashes, metadata = evaluation._verify_local_inputs()

    assert {split: len(rows) for split, rows in cases.items()} == {
        "dev": 22,
        "validation": 10,
        "exposed_test": 8,
    }
    assert metadata["freeze_manifest_sha256"] == "0082546fed1b623a89ab032fd39b5d9aca413817867d34690a1babaa51244f31"
    assert input_hashes["data/real_world/passages.jsonl"] == "365f93b9195733c4c8ce373058a8fe9d38e568c710f22a52b11fd569fe50c1f4"
    assert metadata["lexical_recomputed"] == {
        split: metadata["lexical_report"][split]["lexical_summary"]
        for split in ("dev", "validation", "exposed_test", "overall")
    }


def test_full_jev_comparison_includes_all_splits_and_required_metrics(fixed_cases):
    _, _, metadata = evaluation._verify_local_inputs()
    report = evaluation.build_comparison_report(
        fixed_cases,
        metadata["lexical_report"],
        _outputs(fixed_cases),
    )

    assert report["result_classification"] == "exploratory; not independent blind evidence"
    assert report["judgment_status"].startswith("developer-authored")
    assert "not a blind test" in report["held_out_status"]
    for split, expected_cases in {"dev": 22, "validation": 10, "exposed_test": 8, "overall": 40}.items():
        values = report["splits"][split]
        assert values["cases"] == expected_cases
        assert values["complete"] is True
        assert set(values["comparison"]) == set(evaluation.EXPECTED_METRIC_NAMES)
        assert all(
            row["delta_jev_minus_lexical"] is not None
            for row in values["comparison"].values()
        )


def test_comparison_requires_the_same_fixed_case_and_candidate_universe(fixed_cases):
    _, _, metadata = evaluation._verify_local_inputs()
    outputs = _outputs(fixed_cases)

    _assert_same_case_candidate_universe(
        fixed_cases, outputs, metadata["lexical_report"]
    )
    with pytest.raises(ValueError, match="same fixed 40 cases and 200 candidates"):
        _assert_same_case_candidate_universe(
            fixed_cases, outputs[:-1], metadata["lexical_report"]
        )


def test_paired_bootstrap_resamples_cases_deterministically(fixed_cases):
    _, _, metadata = evaluation._verify_local_inputs()
    report = evaluation.build_comparison_report(
        fixed_cases,
        metadata["lexical_report"],
        _outputs(fixed_cases),
    )

    first = _paired_case_bootstrap(
        fixed_cases, report["splits"], metadata["lexical_report"]
    )
    second = _paired_case_bootstrap(
        fixed_cases, report["splits"], metadata["lexical_report"]
    )

    assert first == second
    assert first["replicates"] == 10_000
    assert first["resampling_unit"].startswith("case_id")
    for split, case_count in {
        "dev": 22,
        "validation": 10,
        "exposed_test": 8,
        "overall": 40,
    }.items():
        values = first["splits"][split]
        assert values["cases"] == case_count
        assert set(values["metrics"]) == set(evaluation.EXPECTED_METRIC_NAMES)
        for metric, interval in values["metrics"].items():
            assert interval["ci_lower"] <= interval["ci_upper"]
            assert interval["point_estimate"] == pytest.approx(
                report["splits"][split]["comparison"][metric][
                    "delta_jev_minus_lexical"
                ]
            )


def test_missing_candidate_invalidates_split_metrics_without_case_dropping(fixed_cases):
    _, _, metadata = evaluation._verify_local_inputs()
    outputs = _outputs(fixed_cases)
    outputs.pop()
    report = evaluation.build_comparison_report(
        fixed_cases,
        metadata["lexical_report"],
        outputs,
    )

    overall = report["splits"]["overall"]
    assert overall["cases"] == 40
    assert overall["complete"] is False
    assert all(value is None for value in overall["jev_summary"].values())
    assert all(
        value["delta_jev_minus_lexical"] is None
        for value in overall["comparison"].values()
    )
    assert sum(row["complete"] for row in overall["per_case"]) == 39


def test_output_digest_is_stable_and_does_not_include_source_bodies(fixed_cases):
    outputs = _outputs(fixed_cases)
    serialized = evaluation._canonical_bytes(outputs)
    assert evaluation._sha256(serialized) == evaluation._sha256(
        evaluation._canonical_bytes(json.loads(serialized))
    )
    assert b"task" not in serialized
    assert b"candidate text" not in serialized


def test_live_result_artifact_is_reproducible_offline():
    report = verify_saved_run()

    assert report["run"]["counts"]["http_request_events"] == 200
    assert report["run"]["counts"]["failed_scores"] == 0
    assert report["run"]["models_returned"] == ["jev-1.14.0"]
    assert report["paired_bootstrap"]["replicates"] == 10_000
    assert report["paired_bootstrap"]["splits"]["overall"]["cases"] == 40
