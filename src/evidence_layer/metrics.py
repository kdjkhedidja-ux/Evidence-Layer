"""Metric definitions for the Phase 2 lexical baseline harness.

``estimated_context_units`` is a whitespace word count ONLY. It is deliberately
not called a token count (no tokenizer is used).
"""

from __future__ import annotations

import math
from collections.abc import Mapping


def estimated_context_units(text: str) -> int:
    """Whitespace word count of ``text`` (an estimated context size, not tokens)."""
    return len(text.split())


def recall_at_k(gold_ids: list[str], selected_ids: list[str]) -> float:
    """|gold ∩ selected| / |gold|.

    Returns 0.0 when there is no gold evidence (zero denominator).
    """
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return len(gold & set(selected_ids)) / len(gold)


def precision_at_k(gold_ids: list[str], selected_ids: list[str]) -> float:
    """|gold ∩ selected| / |selected|.

    Returns 0.0 when nothing was selected (zero denominator).
    """
    selected = set(selected_ids)
    if not selected:
        return 0.0
    return len(set(gold_ids) & selected) / len(selected)


def context_reduction_ratio(all_units: int, selected_units: int) -> float:
    """1 - selected_units / all_units.

    Returns 0.0 when ``all_units`` is zero (zero denominator).
    """
    if all_units <= 0:
        return 0.0
    return 1.0 - (selected_units / all_units)


def mrr_at_k(gold_ids: list[str], selected_ids: list[str]) -> float:
    """Reciprocal rank of the first selected gold candidate (1-based).

    Returns 0.0 if no gold candidate is selected.
    """
    gold = set(gold_ids)
    for rank, candidate_id in enumerate(selected_ids, start=1):
        if candidate_id in gold:
            return 1.0 / rank
    return 0.0


def any_evidence_hit_rate(gold_ids: list[str], selected_ids: list[str]) -> float:
    """Per-case 1.0 if at least one gold candidate is selected, else 0.0.

    The aggregate mean over cases is the any-evidence hit rate.
    """
    return 1.0 if (set(gold_ids) & set(selected_ids)) else 0.0


def full_evidence_rate(gold_ids: list[str], selected_ids: list[str]) -> float:
    """Per-case 1.0 if *all* gold candidates are selected, else 0.0.

    The aggregate mean over cases is the full-evidence rate. When ``k`` is
    smaller than the number of gold IDs this is legitimately 0.0. Returns 0.0
    when there is no gold evidence (zero denominator).
    """
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return 1.0 if gold <= set(selected_ids) else 0.0


def average_precision(gold_ids: list[str], ranked_ids: list[str]) -> float:
    """Average precision over a full ranked candidate list (binary relevance).

    Mean of precision@rank at every rank holding a gold candidate, divided by
    the total number of gold candidates (golds never ranked count as zero).
    Returns 0.0 when there is no gold evidence (zero denominator).
    """
    gold = set(gold_ids)
    if not gold:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, candidate_id in enumerate(ranked_ids, start=1):
        if candidate_id in gold:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / len(gold)


def ndcg_at_k(gold_ids: list[str], ranked_ids: list[str], k: int) -> float:
    """Normalized discounted cumulative gain at ``k`` with binary gains.

    DCG uses gain 1 per gold candidate with a log2(rank+1) discount; the
    ideal ranking places all golds first. Returns 0.0 when there is no gold
    evidence or ``k`` is not positive (zero/invalid denominator).
    """
    gold = set(gold_ids)
    if not gold or k <= 0:
        return 0.0

    def dcg(ids: list[str]) -> float:
        return sum(
            1.0 / math.log2(rank + 1)
            for rank, candidate_id in enumerate(ids[:k], start=1)
            if candidate_id in gold
        )

    ideal_hits = min(len(gold), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    if ideal == 0.0:
        return 0.0
    return dcg(ranked_ids) / ideal


def graded_ndcg_at_k(
    gains: Mapping[str, float], ranked_ids: list[str], k: int
) -> float:
    """Normalized DCG at ``k`` with graded linear gains.

    ``gains`` maps candidate_id to a non-negative relevance grade (higher is
    more relevant); unlisted candidates count as 0. The ideal ranking sorts
    candidates by gain descending. Returns 0.0 when no candidate has a
    positive grade or ``k`` is not positive.
    """
    if k <= 0 or not gains or not any(gain > 0 for gain in gains.values()):
        return 0.0

    def dcg(ids: list[str]) -> float:
        return sum(
            gains.get(candidate_id, 0.0) / math.log2(rank + 1)
            for rank, candidate_id in enumerate(ids[:k], start=1)
        )

    ideal_order = sorted(gains, key=lambda cid: -gains[cid])[:k]
    ideal = sum(
        gains[cid] / math.log2(rank + 1)
        for rank, cid in enumerate(ideal_order, start=1)
        if gains[cid] > 0
    )
    if ideal == 0.0:
        return 0.0
    return dcg(ranked_ids) / ideal

