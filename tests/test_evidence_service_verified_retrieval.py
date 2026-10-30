from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadError,
)
from evidence_service.auth import AuthenticatedRequest, Principal
from evidence_service.secure_snapshot_read import (
    SnapshotState,
    SourceSnapshotManifest,
    VerifiedSnapshot,
    snapshot_object_key,
)
from evidence_service.verified_retrieval import (
    VerifiedRecordError,
    parse_verified_records,
    read_verified_records,
    retrieve_verified_evidence,
)

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE_A = UUID("c1000000-0000-4000-8000-000000000001")
SOURCE_B = UUID("c2000000-0000-4000-8000-000000000002")
VERSION_A = UUID("d1000000-0000-4000-8000-000000000001")
VERSION_B = UUID("d2000000-0000-4000-8000-000000000002")
OWNER = UUID("e1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"
SHA256 = "a" * 64


def auth() -> AuthenticatedRequest:
    return AuthenticatedRequest(
        principal=Principal(ISSUER, SUBJECT), access_token=TOKEN
    )


def manifest(
    source_id: UUID = SOURCE_A, version_id: UUID = VERSION_A
) -> SourceSnapshotManifest:
    return SourceSnapshotManifest(
        workspace_id=WORKSPACE,
        source_id=source_id,
        owner_id=OWNER,
        version_id=version_id,
        object_key=snapshot_object_key(WORKSPACE, source_id, version_id),
        sha256=SHA256,
        size_bytes=0,
        state=SnapshotState.PUBLISHED,
        created_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )


def snapshot(records: list[dict], source_id: UUID = SOURCE_A) -> VerifiedSnapshot:
    content = "".join(json.dumps(record) + "\n" for record in records).encode()
    return VerifiedSnapshot(manifest=manifest(source_id=source_id), content=content)


def record(passage_id: str, text: str, source_id: UUID = SOURCE_A) -> dict:
    return {
        "passage_id": passage_id,
        "source_id": str(source_id),
        "source_url": "https://example.test/source",
        "text": text,
    }


class FakeReadPath:
    def __init__(self, results: dict[UUID, VerifiedSnapshot]) -> None:
        self.results = results
        self.denials: dict[UUID, SourceReadDenialReason] = {}
        self.calls: list[tuple[UUID, UUID]] = []

    def deny(self, source_id: UUID, reason: SourceReadDenialReason) -> None:
        self.denials[source_id] = reason

    async def read(self, auth, *, workspace_id, source_id, version_id):
        self.calls.append((source_id, version_id))
        if source_id in self.denials:
            raise SourceReadError(SourceReadDenial(self.denials[source_id]))
        return self.results[source_id]


def test_parse_verified_records_returns_source_scoped_records() -> None:
    snap = snapshot(
        [
            record("p1", "alpha beta"),
            record("p2", "gamma delta"),
        ]
    )
    records = parse_verified_records(snap)
    assert [item["passage_id"] for item in records] == ["p1", "p2"]
    assert all(item["source_id"] == str(SOURCE_A) for item in records)


def test_parse_verified_records_rejects_cross_source_record() -> None:
    snap = snapshot([record("p1", "alpha beta", source_id=SOURCE_B)])
    with pytest.raises(VerifiedRecordError):
        parse_verified_records(snap)


def test_parse_verified_records_rejects_malformed_line() -> None:
    snap = VerifiedSnapshot(manifest=manifest(), content=b"not-json\n")
    with pytest.raises(VerifiedRecordError):
        parse_verified_records(snap)


def test_parse_verified_records_rejects_missing_fields() -> None:
    snap = VerifiedSnapshot(manifest=manifest(), content=b'{"passage_id": "p1"}\n')
    with pytest.raises(VerifiedRecordError):
        parse_verified_records(snap)


def test_read_verified_records_propagates_denial() -> None:
    path = FakeReadPath({})
    path.deny(SOURCE_A, SourceReadDenialReason.ACCESS_DENIED)
    with pytest.raises(SourceReadError) as excinfo:
        asyncio.run(
            read_verified_records(
                path,
                auth(),
                workspace_id=WORKSPACE,
                source_id=SOURCE_A,
                version_id=VERSION_A,
            )
        )
    assert excinfo.value.denial.reason is SourceReadDenialReason.ACCESS_DENIED


def test_retrieve_returns_only_verified_content() -> None:
    path = FakeReadPath(
        {
            SOURCE_A: snapshot(
                [
                    record("p1", "alpha beta gamma"),
                    record("p2", "alpha delta"),
                ]
            )
        }
    )
    result = asyncio.run(
        retrieve_verified_evidence(
            path,
            auth(),
            workspace_id=WORKSPACE,
            sources=[(SOURCE_A, VERSION_A)],
            query="alpha",
        )
    )
    assert result.status == "ok"
    assert {item.evidence_id for item in result.evidence} == {"p1", "p2"}
    assert all(item.source_id == str(SOURCE_A) for item in result.evidence)


def test_retrieve_fails_closed_when_any_source_is_denied() -> None:
    path = FakeReadPath(
        {
            SOURCE_A: snapshot([record("p1", "alpha beta")]),
        }
    )
    path.deny(SOURCE_B, SourceReadDenialReason.ACCESS_DENIED)
    with pytest.raises(SourceReadError) as excinfo:
        asyncio.run(
            retrieve_verified_evidence(
                path,
                auth(),
                workspace_id=WORKSPACE,
                sources=[(SOURCE_A, VERSION_A), (SOURCE_B, VERSION_B)],
                query="alpha",
            )
        )
    assert excinfo.value.denial.reason is SourceReadDenialReason.ACCESS_DENIED


def test_retrieve_returns_no_results_for_unmatched_query() -> None:
    path = FakeReadPath(
        {
            SOURCE_A: snapshot([record("p1", "alpha beta gamma")]),
        }
    )
    result = asyncio.run(
        retrieve_verified_evidence(
            path,
            auth(),
            workspace_id=WORKSPACE,
            sources=[(SOURCE_A, VERSION_A)],
            query="zzz",
        )
    )
    assert result.status == "no_results"
    assert result.evidence == []

