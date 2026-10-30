"""Retrieval over verified source snapshots (service composition).

The trusted read path returns a ``VerifiedSnapshot`` only after identity,
membership, grant, version, and SHA-256 checks all pass. This module is the only
place those verified bytes are parsed into retrieval records, and it re-checks
that every record is scoped to the verified source before any record is ranked.
A read denial aborts the whole retrieval (fail closed): no partial content is
ever ranked, so rejected source content can never reach retrieval results.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID

from evidence_layer.retrieval.local_corpus import (
    LocalEvidence,
    LocalRetrievalResult,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_NO_RESULTS,
    STATUS_OK,
)
from evidence_layer.schemas import EvidenceCandidate
from evidence_layer.scoring.lexical import LexicalScorer
from evidence_layer.scoring.policy import ProviderSelectionPolicy
from evidence_layer.selection import select_top_k_scored
from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadError,
)
from evidence_service.auth import AuthenticatedRequest
from evidence_service.secure_snapshot_read import (
    SecureSnapshotReadPath,
    VerifiedSnapshot,
)

_REQUIRED_RECORD_FIELDS = ("passage_id", "source_id", "source_url", "text")


class VerifiedRecordError(Exception):
    """Verified bytes were not well-formed, source-scoped evidence records."""


def parse_verified_records(snapshot: VerifiedSnapshot) -> list[dict[str, object]]:
    """Parse verified snapshot bytes into evidence records, re-checking scope.

    Every record must carry the required retrieval fields and its ``source_id``
    must equal the verified manifest's source. Any malformed line or scope
    mismatch fails closed, so nothing unverified or cross-source is ever ranked.
    """
    try:
        text = snapshot.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerifiedRecordError from exc

    records: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise VerifiedRecordError from exc
        if not isinstance(record, dict):
            raise VerifiedRecordError
        for key in _REQUIRED_RECORD_FIELDS:
            if not isinstance(record.get(key), str) or not record[key]:
                raise VerifiedRecordError
        if record["source_id"] != str(snapshot.manifest.source_id):
            raise VerifiedRecordError
        records.append(record)
    return records


async def read_verified_records(
    path: SecureSnapshotReadPath,
    auth: AuthenticatedRequest,
    *,
    workspace_id: UUID,
    source_id: UUID,
    version_id: UUID,
) -> list[dict[str, object]]:
    """Read one source through the trusted path and parse its verified bytes.

    A denial raised by the trusted path (``SourceReadError``) propagates
    unchanged; no fallback or partial parse occurs.
    """
    snapshot = await path.read(
        auth,
        workspace_id=workspace_id,
        source_id=source_id,
        version_id=version_id,
    )
    return parse_verified_records(snapshot)


def _require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


async def retrieve_verified_evidence(
    path: SecureSnapshotReadPath,
    auth: AuthenticatedRequest,
    *,
    workspace_id: UUID,
    sources: Sequence[tuple[UUID, UUID]],
    query: str,
    top_k: int = 5,
    budget_words: int = 500,
) -> LocalRetrievalResult:
    """Retrieve ranked evidence only from fully verified source snapshots.

    Every source is read and verified before any ranking happens. If any read is
    denied, the denial propagates and no result is produced (fail closed).
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    _require_positive_int("top_k", top_k)
    _require_positive_int("budget_words", budget_words)
    if not sources:
        raise ValueError("sources must not be empty")

    # Read everything first; a denial aborts before any ranking.
    records: dict[str, dict[str, object]] = {}
    try:
        for source_id, version_id in sources:
            for record in await read_verified_records(
                path,
                auth,
                workspace_id=workspace_id,
                source_id=source_id,
                version_id=version_id,
            ):
                passage_id = str(record["passage_id"])
                if passage_id in records:
                    raise VerifiedRecordError
                records[passage_id] = record
    except VerifiedRecordError as exc:
        raise SourceReadError(
            SourceReadDenial(SourceReadDenialReason.INVALID_SOURCE_DATA)
        ) from exc

    scorer = LexicalScorer()
    policy = ProviderSelectionPolicy()
    query_tokens = set(scorer.normalize(query.strip()))
    candidates: list[EvidenceCandidate] = []
    if query_tokens:
        candidates = [
            EvidenceCandidate(
                candidate_id=str(record["passage_id"]),
                source_id=str(record["source_id"]),
                source_url=str(record["source_url"]),
                text=str(record["text"]),
            )
            for record in records.values()
            if query_tokens & set(scorer.normalize(str(record["text"])))
        ]

    outcome = None
    ranked_ids: list[str] = []
    scores_by_id: dict[str, float] = {}
    if candidates:
        outcome = policy.score_candidates(query, candidates)
        ranked_ids = select_top_k_scored(
            candidates, outcome.scores, len(candidates) or 1
        )
        scores_by_id = {
            candidate.candidate_id: score
            for candidate, score in zip(candidates, outcome.scores)
        }

    evidence: list[LocalEvidence] = []
    remaining = budget_words
    for passage_id in ranked_ids:
        if len(evidence) >= top_k:
            break
        record = records[passage_id]
        text = str(record["text"])
        words = len(text.split())
        if words == 0 or words > remaining:
            continue
        remaining -= words
        evidence.append(
            LocalEvidence(
                evidence_id=passage_id,
                text=text,
                source_id=str(record["source_id"]),
                source_url=str(record["source_url"]),
                section=(
                    str(record["section"])
                    if record.get("section") is not None
                    else None
                ),
                score=scores_by_id[passage_id],
                word_count=words,
            )
        )

    if evidence:
        status = STATUS_OK
    elif candidates:
        status = STATUS_BUDGET_EXHAUSTED
    else:
        status = STATUS_NO_RESULTS

    return LocalRetrievalResult(
        status=status,
        query=query.strip(),
        top_k=top_k,
        budget_words=budget_words,
        evidence=evidence,
        candidate_count=len(candidates),
        used_words=sum(item.word_count for item in evidence),
        scoring_provider=outcome.provider if outcome is not None else "lexical",
        scoring_failure_category=(
            outcome.fallback_reason.category
            if outcome is not None and outcome.fallback_reason is not None
            else None
        ),
    )

