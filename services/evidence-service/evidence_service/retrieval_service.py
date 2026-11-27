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
    identifier_boost,
    query_identifiers,
    source_path,
)
from evidence_service.connector_parts import PartLocation, parse_part_label
from evidence_service.evidence_repository import EvidenceRecord

MIN_RELATIVE_SCORE = 0.35
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
    # Exact-identifier boost (EVIDENCE_IDENTIFIER_BOOST_ENABLED): ``score``
    # keeps its unboosted value; ranking used ``boosted_score``.
    boost: float = 1.0

    @property
    def boosted_score(self) -> float:
        return self.score * self.boost


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
    protected: bool = False
    part: PartLocation | None = None
    boost: float = 1.0

    @property
    def rank_score(self) -> float:
        return self.score * self.boost

    @property
    def file_range(self) -> tuple[str, int, int] | None:
        """(file label, absolute start, absolute end) for a chunk of a part."""
        if self.part is None:
            return None
        return (
            self.part.base_label,
            self.part.start + self.chunk.start,
            self.part.start + self.chunk.end,
        )

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
    identifier_boost_enabled: bool = False,
) -> RetrievalSelection:
    scorer = LexicalScorer()
    query_terms = set(scorer.normalize(query))
    identifiers = query_identifiers(query) if identifier_boost_enabled else frozenset()
    counter, estimator = token_counter()
    candidates: list[_ChunkCandidate] = []
    chunk_count = 0
    matched_chunk_count = 0
    for record in records:
        part = parse_part_label(record.source_label)
        path = source_path(record.source_label)
        # A later part starts mid-file: no intro boost and no title taken
        # from a heading that is not the document's own.
        later_part = part is not None and part.index > 1
        title = document_title("" if later_part else (record.content or ""), path)
        for chunk in chunk_text(record.content or ""):
            chunk_count += 1
            raw_score = scorer.score(query, chunk.content)
            related_score = (related_record_scores or {}).get(record.id, 0)
            code_boost = identifier_boost(identifiers, path=path, content=chunk.content)
            lexical_score = (
                adjusted_score(
                    raw_score,
                    path=path,
                    title=title,
                    query_terms=query_terms,
                    query=query,
                    chunk_index=chunk.index + (1 if later_part else 0),
                )
                if raw_score > 0 else 0
            )
            if lexical_score > 0:
                matched_chunk_count += 1
            chunk_key = f"{record.id}:{chunk.start}:{chunk.end}"
            if score_overrides is not None:
                # Provider (JEV) score, reported unchanged; when enabled the
                # exact-identifier boost only changes the ranking (rank_score).
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
                    protected=bool(
                        (seed_record_ids and record.id in seed_record_ids)
                        or (related_record_scores and record.id in related_record_scores)
                    ),
                    part=part,
                    boost=code_boost,
                )
            )
    candidates.sort(
        key=lambda item: (
            0 if seed_record_ids and item.record.id in seed_record_ids else
            1 if related_record_scores and item.record.id in related_record_scores else 2,
            -item.rank_score,
            item.path or "",
            str(item.record.id),
            item.chunk.start,
        )
    )

    selected: list[SelectedEvidence] = []
    selected_chunks: set[str] = set()
    # Absolute character ranges already selected per large file, so the
    # overlap between two parts is never returned twice.
    selected_file_ranges: dict[str, list[tuple[int, int]]] = {}
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
            file_range = candidate.file_range
            if file_range is not None and any(
                file_range[1] < end and start < file_range[2]
                for start, end in selected_file_ranges.get(file_range[0], ())
            ):
                continue
            if per_parent.get(candidate.parent_key, 0) >= parent_limit:
                continue
            if selected and not candidate.protected:
                floor = max(item.boosted_score for item in selected) * MIN_RELATIVE_SCORE
                if candidate.rank_score < floor:
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
                    boost=candidate.boost,
                )
            )
            selected_chunks.add(candidate.chunk_key)
            if file_range is not None:
                selected_file_ranges.setdefault(file_range[0], []).append(
                    (file_range[1], file_range[2])
                )
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
