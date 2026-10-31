"""Tests for the Phase 2 metric definitions, including zero denominators."""

import pytest

from evidence_layer.metrics import (
    any_evidence_hit_rate,
    average_precision,
    context_reduction_ratio,
    estimated_context_units,
    full_evidence_rate,
    graded_ndcg_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_recall_at_k_exact():
    assert recall_at_k(["a", "b"], ["a", "c"]) == 0.5
    assert recall_at_k(["a"], ["a"]) == 1.0
    assert recall_at_k(["a"], ["b"]) == 0.0


def test_recall_at_k_zero_gold_denominator():
    assert recall_at_k([], ["a"]) == 0.0


def test_precision_at_k_exact():
    assert precision_at_k(["a", "b"], ["a", "c"]) == 0.5
    assert precision_at_k(["a"], ["a"]) == 1.0
    assert precision_at_k(["a", "b"], ["a", "b", "c"]) == 2 / 3


def test_precision_at_k_zero_selected_denominator():
    assert precision_at_k(["a"], []) == 0.0


def test_context_reduction_ratio():
    assert context_reduction_ratio(100, 20) == 0.8
    assert context_reduction_ratio(100, 0) == 1.0


def test_context_reduction_ratio_zero_all_units():
    assert context_reduction_ratio(0, 0) == 0.0


def test_estimated_context_units_is_whitespace_word_count():
    assert estimated_context_units("one two three") == 3
    assert estimated_context_units("") == 0
    assert estimated_context_units("  spaced   out  ") == 2


def test_mrr_at_k():
    assert mrr_at_k(["a"], ["a", "b", "c"]) == 1.0
    assert mrr_at_k(["b"], ["a", "b", "c"]) == 0.5
    assert mrr_at_k(["c"], ["a", "b", "c"]) == pytest.approx(1 / 3)
    assert mrr_at_k(["d"], ["a", "b", "c"]) == 0.0


def test_any_evidence_hit_rate():
    assert any_evidence_hit_rate(["a", "b"], ["a"]) == 1.0
    assert any_evidence_hit_rate(["a", "b"], ["c"]) == 0.0


def test_full_evidence_rate():
    assert full_evidence_rate(["a"], ["a"]) == 1.0
    assert full_evidence_rate(["a", "b"], ["a", "b", "c"]) == 1.0
    assert full_evidence_rate(["a", "b"], ["a", "c"]) == 0.0


def test_full_evidence_rate_below_gold_count_is_zero():
    # k=1 < 2 gold IDs -> all gold cannot be selected, so full rate is 0.0
    assert full_evidence_rate(["a", "b"], ["a"]) == 0.0


def test_full_evidence_rate_zero_gold_denominator():
    assert full_evidence_rate([], ["a"]) == 0.0


def test_average_precision_exact():
    # gold at ranks 1 and 3: AP = (1/1 + 2/3) / 2
    assert average_precision(["a", "b"], ["a", "c", "b"]) == pytest.approx(
        (1.0 + 2 / 3) / 2
    )
    assert average_precision(["a"], ["a"]) == 1.0
    assert average_precision(["a"], ["b", "c"]) == 0.0


def test_average_precision_unranked_gold_counts_zero():
    # One gold never appears in the ranking; the denominator still includes it.
    assert average_precision(["a", "b"], ["a"]) == 0.5


def test_average_precision_zero_gold_denominator():
    assert average_precision([], ["a"]) == 0.0


def test_ndcg_at_k_exact():
    import math

    # One gold at rank 2 within k=3; ideal puts it at rank 1.
    value = ndcg_at_k(["a"], ["x", "a", "y"], 3)
    assert value == pytest.approx(1.0 / math.log2(3))
    assert ndcg_at_k(["a"], ["a", "x"], 1) == 1.0
    assert ndcg_at_k(["a"], ["x", "a"], 1) == 0.0


def test_ndcg_at_k_multiple_golds_ideal_first():
    import math

    # Two golds both inside k=2 in ideal order -> 1.0
    assert ndcg_at_k(["a", "b"], ["a", "b", "x"], 2) == 1.0
    # Only one of two golds inside k=2 -> half the ideal DCG.
    ideal = 1.0 + 1.0 / math.log2(3)
    assert ndcg_at_k(["a", "b"], ["a", "x", "b"], 2) == pytest.approx(1.0 / ideal)


def test_ndcg_at_k_zero_denominators():
    assert ndcg_at_k([], ["a"], 3) == 0.0
    assert ndcg_at_k(["a"], ["a"], 0) == 0.0


def test_graded_ndcg_at_k_exact():
    import math

    gains = {"full": 2.0, "part": 1.0}
    # Ideal order is [full, part]; any other order scores below 1.
    assert graded_ndcg_at_k(gains, ["full", "part", "x"], 2) == 1.0
    value = graded_ndcg_at_k(gains, ["part", "full", "x"], 2)
    ideal = 2.0 + 1.0 / math.log2(3)
    assert value == pytest.approx((1.0 + 2.0 / math.log2(3)) / ideal)
    # Unranked relevant candidates count as zero gain positions.
    assert graded_ndcg_at_k(gains, ["x", "y"], 2) == 0.0


def test_graded_ndcg_at_k_zero_denominators():
    assert graded_ndcg_at_k({}, ["a"], 3) == 0.0
    assert graded_ndcg_at_k({"a": 0.0}, ["a"], 3) == 0.0
    assert graded_ndcg_at_k({"a": 1.0}, ["a"], 0) == 0.0

