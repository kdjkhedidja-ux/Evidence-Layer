"""Blind review packaging and disagreement handling for the real-world set."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_real_world_dataset as rw  # noqa: E402
from prepare_real_world_review import build_review_artifacts  # noqa: E402
from reconcile_real_world_review import reconcile_reviews  # noqa: E402


@pytest.fixture(scope="module")
def review_artifacts():
    cases = [case for split in rw.build_real_world_cases() for case in split]
    return build_review_artifacts(cases, seed=731)


def _reviews(key: dict) -> list[dict]:
    return [
        {
            "case_token": case_token,
            "candidate_token": candidate_token,
            "grade": item["developer_grade"],
        }
        for case_token, case in key["cases"].items()
        for candidate_token, item in case["candidates"].items()
    ]


def test_blind_pack_omits_existing_judgments_and_source_identifiers(review_artifacts):
    pack, key = review_artifacts
    rendered = json.dumps(pack)
    assert len(pack["cases"]) == 40
    assert "developer_grade" not in rendered
    assert "relevance_scores" not in rendered
    assert "gold_candidate_ids" not in rendered
    assert "source_id" not in rendered
    assert "source_url" not in rendered
    assert "case_type" not in rendered
    assert "required_facts" not in rendered
    assert "developer_grade" in json.dumps(key)
    for case in pack["cases"]:
        assert set(case) == {"case_token", "question", "candidates"}
        assert all(set(candidate) == {"candidate_token", "text"} for candidate in case["candidates"])


def test_review_pack_generation_is_deterministic_but_blinded(review_artifacts):
    cases = [case for split in rw.build_real_world_cases() for case in split]
    assert review_artifacts == build_review_artifacts(cases, seed=731)
    other_pack, _ = build_review_artifacts(cases, seed=732)
    assert review_artifacts[0] != other_pack


def test_review_agreement_is_reported_after_private_mapping(review_artifacts):
    _, key = review_artifacts
    reviews = _reviews(key)
    result = reconcile_reviews(key, reviews, reviews)
    assert result["candidate_count"] == 200
    assert result["agreement_count"] == 200
    assert result["two_reviewer_disagreement_count"] == 0
    assert result["adjudication_status"] == "complete"
    assert all(row["resolution"] == "two_reviewer_agreement" for row in result["results"])
    assert all("developer_grade" in row for row in result["results"])


def test_disagreement_gets_blind_third_review_then_panel_if_all_grades_differ(review_artifacts):
    _, key = review_artifacts
    reviewer_a = _reviews(key)
    reviewer_b = [dict(row) for row in reviewer_a]
    target = reviewer_b[0]
    target["grade"] = (target["grade"] + 1) % 3

    pending = reconcile_reviews(key, reviewer_a, reviewer_b)
    assert pending["two_reviewer_disagreement_count"] == 1
    assert pending["adjudication_status"] == "pending"
    assert pending["disagreements_for_third_review"][0].keys() == {
        "case_token", "candidate_token", "question", "text"
    }
    assert "developer_grade" not in json.dumps(pending["disagreements_for_third_review"])
    assert "reviewer_a_grade" not in json.dumps(pending["disagreements_for_third_review"])

    dispute = pending["disagreements_for_third_review"][0]
    third_review = [{**dispute, "grade": target["grade"]}]
    resolved = reconcile_reviews(key, reviewer_a, reviewer_b, third_review)
    assert resolved["adjudication_status"] == "complete"
    assert resolved["two_reviewer_disagreement_count"] == 1
    disputed_result = next(row for row in resolved["results"] if row["resolution"] == "majority_after_blind_third_review")
    assert disputed_result["independent_grade"] == target["grade"]

    third_grade = (target["grade"] + 1) % 3
    three_way = reconcile_reviews(
        key,
        reviewer_a,
        reviewer_b,
        [{**dispute, "grade": third_grade}],
    )
    assert three_way["adjudication_status"] == "pending"
    assert len(three_way["disagreements_for_panel"]) == 1
    panel_item = three_way["disagreements_for_panel"][0]
    panel = [{
        "case_token": panel_item["case_token"],
        "candidate_token": panel_item["candidate_token"],
        "grade": 2,
        "rationale": "Panel reviewed the question and evidence against the documented rubric.",
    }]
    finalized = reconcile_reviews(
        key, reviewer_a, reviewer_b, [{**dispute, "grade": third_grade}], panel
    )
    assert finalized["adjudication_status"] == "complete"
    panel_result = next(row for row in finalized["results"] if row["resolution"] == "panel_adjudicated")
    assert panel_result["independent_grade"] == 2
    assert panel_result["panel_rationale"]


def test_incomplete_or_invalid_review_is_rejected(review_artifacts):
    _, key = review_artifacts
    full = _reviews(key)
    with pytest.raises(ValueError, match="missing"):
        reconcile_reviews(key, full[:-1], full)
    invalid = [dict(row) for row in full]
    invalid[0]["grade"] = True
    with pytest.raises(ValueError, match="invalid grade"):
        reconcile_reviews(key, invalid, full)
