"""Deterministic, workspace-scoped evidence selection for the API-key endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable

from evidence_layer.scoring.lexical import LexicalScorer
from evidence_layer.tokenization import shared_visible_counter

from evidence_service.evidence_repository import EvidenceRecord

_counter_cache: tuple[Callable[[str], int], str] | None = None


def token_counter() -> tuple[Callable[[str], int], str]:
    """Use the pinned visible tokenizer when its artifact is installed; else estimate."""
    global _counter_cache
    if _counter_cache is None:
        try:
            counter, tokenizer_id, _ = shared_visible_counter()
            _counter_cache = (counter, tokenizer_id)
        except Exception:
            _counter_cache = (
                lambda text: max(1, ceil(len(text) / 4)),
                "chars_div_4",
            )
    return _counter_cache


@dataclass(frozen=True)
class SelectedEvidence:
    record: EvidenceRecord
    score: float
    token_count: int
    char_count: int


@dataclass(frozen=True)
class RetrievalSelection:
    status: str
    selected: list[SelectedEvidence]
    candidate_count: int
    used_tokens: int
    used_chars: int
    token_estimator: str


def select_evidence(
    records: list[EvidenceRecord],
    *,
    query: str,
    top_k: int,
    max_tokens: int,
    max_chars: int,
) -> RetrievalSelection:
    scorer = LexicalScorer()
    counter, estimator = token_counter()
    scored: list[tuple[float, EvidenceRecord]] = []
    for record in records:
        text = record.content or ""
        score = scorer.score(query, text)
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].id)))

    selected: list[SelectedEvidence] = []
    used_tokens = 0
    used_chars = 0
    budget_hit = False
    for score, record in scored:
        text = record.content or ""
        chars = len(text)
        tokens = counter(text)
        if used_tokens + tokens > max_tokens or used_chars + chars > max_chars:
            budget_hit = True
            continue
        selected.append(
            SelectedEvidence(
                record=record,
                score=score,
                token_count=tokens,
                char_count=chars,
            )
        )
        used_tokens += tokens
        used_chars += chars
        if len(selected) >= top_k:
            break

    if not selected:
        status = "no_results"
    elif budget_hit and len(selected) < top_k:
        status = "budget_exhausted"
    else:
        status = "ok"
    return RetrievalSelection(
        status=status,
        selected=selected,
        candidate_count=len(records),
        used_tokens=used_tokens,
        used_chars=used_chars,
        token_estimator=estimator,
    )
