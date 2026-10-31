"""Invariants for the Phase 6 targeted extension evaluation set."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_dataset  # noqa: E402
import build_extension_dataset as bx  # noqa: E402
from evidence_layer.schemas import EvaluationCase  # noqa: E402


@pytest.fixture(scope="module")
def extension_cases():
    return bx.build_extension_cases()


def test_extension_size_and_split(extension_cases):
    dev, test = extension_cases
    assert len(dev) + len(test) == 51
    assert len(test) >= 10  # large enough to measure, small enough to stay held out
    assert {c["case_id"] for c in dev}.isdisjoint({c["case_id"] for c in test})


def test_extension_schema_valid(extension_cases):
    dev, test = extension_cases
    for raw in dev + test:
        case = EvaluationCase(**raw)
        assert case.task.input.strip()
        assert case.required_facts
        gold = set(case.gold_candidate_ids)
        candidate_ids = {c.candidate_id for c in case.candidates}
        assert gold <= candidate_ids
        assert len(case.candidates) == 5
        assert len(candidate_ids) == 5


def test_extension_case_types_cover_target_gaps(extension_cases):
    dev, test = extension_cases
    counts = Counter(c["case_type"] for c in dev + test)
    assert counts == {
        "hard_negative": 16,
        "partial_relevance": 10,
        "coverage": 10,
        "compound_query": 10,
        "context_dependent": 5,
    }


def test_extension_gold_passages_disjoint_from_base_set(extension_cases):
    dev, test = extension_cases
    base_dev, base_test = build_dataset.build_cases()
    base_golds = {g for c in base_dev + base_test for g in c["gold_candidate_ids"]}
    ext_golds = {g for c in dev + test for g in c["gold_candidate_ids"]}
    assert base_golds.isdisjoint(ext_golds)


def test_extension_split_keeps_gold_passages_disjoint(extension_cases):
    dev, test = extension_cases
    dev_golds = {g for c in dev for g in c["gold_candidate_ids"]}
    test_golds = {g for c in test for g in c["gold_candidate_ids"]}
    assert dev_golds.isdisjoint(test_golds)


def test_extension_split_membership_is_pinned(extension_cases):
    # The split must be reproducible under the pinned extension seed; record
    # the held-out membership so an accidental change is loud.
    _, test = extension_cases
    membership = sorted(c["case_id"] for c in test)
    assert membership == [
        "xcase_0001",
        "xcase_0008",
        "xcase_0009",
        "xcase_0012",
        "xcase_0013",
        "xcase_0024",
        "xcase_0025",
        "xcase_0032",
        "xcase_0035",
        "xcase_0045",
        "xcase_0047",
    ]


def test_hard_negative_cases_include_crafted_confusers(extension_cases):
    dev, test = extension_cases
    for case in dev + test:
        if case["case_type"] != "hard_negative":
            continue
        candidate_ids = {c["candidate_id"] for c in case["candidates"]}
        confusers = {cid for cid in candidate_ids if cid.startswith("hn_")}
        assert confusers, f"{case['case_id']} lacks a crafted confuser candidate"
        assert confusers.isdisjoint(case["gold_candidate_ids"])


def test_partial_relevance_cases_carry_graded_scores(extension_cases):
    dev, test = extension_cases
    for case in dev + test:
        if case["case_type"] != "partial_relevance":
            continue
        scores = case["relevance_scores"]
        assert scores, f"{case['case_id']} lacks relevance grades"
        assert all(scores[g] == 2 for g in case["gold_candidate_ids"])
        assert 1 in scores.values()  # at least one partially relevant candidate


def test_extension_uses_multiple_publishers(extension_cases):
    dev, test = extension_cases
    publishers = {
        candidate["source_id"] for case in dev + test for candidate in case["candidates"]
    }
    assert publishers == {"opsrunbook/2026", "streamspec/2.1", "cloudtier/4"}


def test_extension_deterministic(extension_cases):
    assert extension_cases == bx.build_extension_cases()
