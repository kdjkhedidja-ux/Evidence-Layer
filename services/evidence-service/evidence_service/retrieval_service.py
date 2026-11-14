"""Deterministic, workspace-scoped evidence selection for the API-key endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable
from uuid import UUID

from evidence_layer.scoring.lexical import LexicalScorer
from evidence_layer.tokenization import shared_visible_counter

from evidence_service.chunking import (
    CHUNKING_VERSION,
    TextChunk,
    adjusted_score,
    chunk_text,
    document_title,
    source_path,
)
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
    content: str
    score: float
    token_count: int
    char_count: int
    chunk_id: str
    chunk_index: int
    chunk_start: int
    chunk_end: int
    path: str | None
    title: str | None


@dataclass(frozen=True)
class RetrievalSelection:
    status: str
    selected: list[SelectedEvidence]
    candidate_count: int
    chunk_count: int
    matched_chunk_count: int
    used_tokens: int
    used_chars: int
    token_estimator: str
    chunking: str = CHUNKING_VERSION


@dataclass(frozen=True)
class _ChunkCandidate:
    record: EvidenceRecord
    chunk: TextChunk
    path: str | None
    title: str | None
    score: float
    token_count: int
    char_count: int

    @property
    def parent_key(self) -> str:
        return str(self.record.id)

    @property
    def chunk_key(self) -> str:
        return f"{self.record.id}:{self.chunk.start}:{self.chunk.end}"


def select_evidence(
    records: list[EvidenceRecord],
    *,
    query: str,
    top_k: int,
    max_tokens: int,
    max_chars: int,
    seed_record_ids: set[UUID] | None = None,
    related_record_scores: dict[UUID, float] | None = None,
    score_overrides: dict[str, float] | None = None,
) -> RetrievalSelection:
    scorer = LexicalScorer()
    query_terms = set(scorer.normalize(query))
    counter, estimator = token_counter()
    candidates: list[_ChunkCandidate] = []
    chunk_count = 0
    matched_chunk_count = 0
    for record in records:
        path = source_path(record.source_label)
        title = document_title(record.content or "", path)
        for chunk in chunk_text(record.content or ""):
            chunk_count += 1
            raw_score = scorer.score(query, chunk.content)
            related_score = (related_record_scores or {}).get(record.id, 0)
            lexical_score = (
                adjusted_score(
                    raw_score,
                    path=path,
                    title=title,
                    query_terms=query_terms,
                    query=query,
                    chunk_index=chunk.index,
                )
                if raw_score > 0 else 0
            )
            if lexical_score > 0:
                matched_chunk_count += 1
            chunk_key = f"{record.id}:{chunk.start}:{chunk.end}"
            if score_overrides is not None:
                score = score_overrides.get(chunk_key, 0)
                if score <= 0:
                    continue
            else:
                if raw_score <= 0 and related_score <= 0:
                    continue
                score = max(lexical_score, related_score)
                if score <= 0:
                    continue
            candidates.append(
                _ChunkCandidate(
                    record=record,
                    chunk=chunk,
                    path=path,
                    title=title,
                    score=score,
                    token_count=counter(chunk.content),
                    char_count=len(chunk.content),
                )
            )
    candidates.sort(
        key=lambda item: (
            0 if seed_record_ids and item.record.id in seed_record_ids else
            1 if related_record_scores and item.record.id in related_record_scores else 2,
            -item.score,
            item.path or "",
            str(item.record.id),
            item.chunk.start,
        )
    )

    selected: list[SelectedEvidence] = []
    selected_chunks: set[str] = set()
    per_parent: dict[str, int] = {}
    used_tokens = 0
    used_chars = 0
    budget_hit = False
    for parent_limit in (1, 2, max(1, top_k)):
        if len(selected) >= top_k:
            break
        for candidate in candidates:
            if len(selected) >= top_k:
                break
            if candidate.chunk_key in selected_chunks:
                continue
            if per_parent.get(candidate.parent_key, 0) >= parent_limit:
                continue
            if (
                used_tokens + candidate.token_count > max_tokens
                or used_chars + candidate.char_count > max_chars
            ):
                budget_hit = True
                continue
            selected.append(
                SelectedEvidence(
                    record=candidate.record,
                    content=candidate.chunk.content,
                    score=candidate.score,
                    token_count=candidate.token_count,
                    char_count=candidate.char_count,
                    chunk_id=candidate.chunk_key,
                    chunk_index=candidate.chunk.index,
                    chunk_start=candidate.chunk.start,
                    chunk_end=candidate.chunk.end,
                    path=candidate.path,
                    title=candidate.title,
                )
            )
            selected_chunks.add(candidate.chunk_key)
            per_parent[candidate.parent_key] = per_parent.get(candidate.parent_key, 0) + 1
            used_tokens += candidate.token_count
            used_chars += candidate.char_count

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
        chunk_count=chunk_count,
        matched_chunk_count=matched_chunk_count,
        used_tokens=used_tokens,
        used_chars=used_chars,
        token_estimator=estimator,
    )
