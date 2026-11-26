"""Large connector files: parts, offsets, overlap dedup, recorded non-indexed files."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from evidence_service.chunking import source_path
from evidence_service.config import SupabaseSettings
from evidence_service.connector_parts import (
    MAX_PARTS,
    PART_MAX_CHARS,
    PART_OVERLAP_CHARS,
    SINGLE_RECORD_MAX_CHARS,
    log_path_hash,
    not_indexed_reason,
    parse_part_label,
    split_parts,
)
from evidence_service.connector_repository import ConnectorRepository
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.github_app_sync_service import GitHubAppSyncService
from evidence_service.retrieval_service import select_evidence
from test_github_app_sync_service import CONNECTION, FakeRepository

WORKSPACE = UUID("33333333-3333-4333-8333-333333333333")
FILE_ID = "acme/docs/services/app.py"


def big_source(lines: int = 5_000) -> str:
    return "".join(f"def function_{index:05d}(value):\n    return value + {index}\n" for index in range(lines))


def part_records(text: str) -> list[EvidenceRecord]:
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    return [
        EvidenceRecord(
            id=uuid4(),
            workspace_id=WORKSPACE,
            content=record["content"],
            source_label=record["source_label"],
            created_at=now,
            updated_at=now,
        )
        for record in GitHubAppSyncService._evidence_records(FILE_ID, text, "rev-1")
    ]


# --- splitting ------------------------------------------------------------------


def test_small_files_stay_one_record_with_their_own_id() -> None:
    text = "x" * SINGLE_RECORD_MAX_CHARS
    records = GitHubAppSyncService._evidence_records(FILE_ID, text, "rev")

    assert split_parts(text) == [(0, len(text))]
    assert records == [{
        "content": text,
        "source_label": f"GitHub: {FILE_ID}",
        "connector_external_id": FILE_ID,
        "connector_revision": "rev",
    }]


def test_large_files_split_on_lines_with_overlap_and_bounded_parts() -> None:
    text = big_source()
    ranges = split_parts(text)

    assert len(ranges) > 1
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    for (start, end), (next_start, _) in zip(ranges, ranges[1:]):
        assert end - start <= PART_MAX_CHARS
        assert text[end - 1] == "\n" and text[next_start - 1] == "\n"
        assert end - next_start >= PART_OVERLAP_CHARS
    assert ranges[-1][1] - ranges[-1][0] <= PART_MAX_CHARS


def test_a_file_at_the_fetch_limit_fits_the_part_cap() -> None:
    worst = (("z" * 5_999 + "\n") * 400)[: 2 * 1024 * 1024]
    assert len(split_parts(worst)) <= MAX_PARTS
    assert len(split_parts("q" * 2 * 1024 * 1024)) <= MAX_PARTS


# --- offsets map back to the original file (point 4) ----------------------------


def test_part_labels_carry_real_offsets_into_the_original_file() -> None:
    text = big_source()
    records = GitHubAppSyncService._evidence_records(FILE_ID, text, "rev")

    assert [record["connector_external_id"] for record in records] == [
        f"{FILE_ID}#part-{index:03d}" for index in range(1, len(records) + 1)
    ]
    for index, record in enumerate(records, start=1):
        location = parse_part_label(record["source_label"])
        assert location is not None
        assert (location.index, location.total) == (index, len(records))
        assert location.base_label == f"GitHub: {FILE_ID}"
        assert text[location.start:location.end] == record["content"]
        assert len(record["content"]) <= PART_MAX_CHARS
        assert source_path(record["source_label"]) == "services/app.py"


def test_retrieved_chunks_map_back_to_the_original_file() -> None:
    text = big_source()
    records = part_records(text)
    query = "function_04000 function_02500 function_00010 value"

    result = select_evidence(records, query=query, top_k=8, max_tokens=8_000, max_chars=40_000)

    assert result.selected
    for item in result.selected:
        location = parse_part_label(item.record.source_label)
        start = location.start + item.chunk_start
        end = location.start + item.chunk_end
        assert text[start:end] == item.content
        assert item.path == "services/app.py"


# --- overlap between parts is never returned twice (point 5) --------------------


def test_one_result_never_returns_overlapping_text_from_two_parts() -> None:
    text = big_source()
    ranges = split_parts(text)
    # A term that only exists inside the overlap of parts 1 and 2.
    overlap_start, overlap_end = ranges[1][0], ranges[0][1]
    middle = (overlap_start + overlap_end) // 2
    line_start = text.rfind("\n", 0, middle) + 1
    text = text[:line_start] + "# overlapmarker overlapmarker\n" + text[line_start:]
    records = part_records(text)
    assert sum("overlapmarker" in record.content for record in records) == 2

    result = select_evidence(records, query="overlapmarker", top_k=10, max_tokens=20_000, max_chars=80_000)

    spans = []
    for item in result.selected:
        location = parse_part_label(item.record.source_label)
        spans.append((location.start + item.chunk_start, location.start + item.chunk_end))
    spans.sort()
    for (start, end), (next_start, _) in zip(spans, spans[1:]):
        assert next_start >= end, "two selected chunks share text from the same file"
    assert sum(item.content.count("overlapmarker") for item in result.selected) == 2  # one line, twice


# --- sync: parts, stale handling, recorded non-indexed files --------------------


def repo_handler(files: dict[str, tuple[bytes, str]], *, sizes: dict[str, int] | None = None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{
                "name": "docs", "default_branch": "main", "owner": {"login": "acme"},
            }]})
        if request.url.path == "/repos/acme/docs/git/trees/main":
            return httpx.Response(200, json={"truncated": False, "tree": [
                {"path": path, "type": "blob", "sha": f"sha-{path}-{len(body)}",
                 "size": (sizes or {}).get(path, len(body))}
                for path, (body, _) in files.items()
            ]})
        if request.url.path.startswith("/repos/acme/docs/contents/"):
            path = request.url.path.split("/contents/", 1)[1]
            if calls is not None:
                calls.append(path)
            body, content_type = files[path]
            if content_type == "error":
                return httpx.Response(500)
            return httpx.Response(200, content=body, headers={"content-type": content_type})
        return httpx.Response(404)

    return handler


def stateful_repository() -> FakeRepository:
    """Upserts and evidence writes accumulate like the real tables."""
    repository = FakeRepository()
    stored: dict[str, dict] = {}
    evidence: dict[str, dict] = {}
    repository.stored = stored  # type: ignore[attr-defined]
    repository.evidence = evidence  # type: ignore[attr-defined]
    repository.evidence_calls = []  # type: ignore[attr-defined]

    async def upsert_documents(access_token, documents):
        stored.update({document["external_id"]: document for document in documents})

    async def document_states(access_token, connection_id):
        return {key: (value["revision"], value["mime_type"]) for key, value in stored.items()}

    async def upsert_evidence_documents(access_token, *, workspace_id, connection_id, documents):
        repository.evidence_calls.append(len(documents))  # type: ignore[attr-defined]
        evidence.update({document["connector_external_id"]: document for document in documents})

    async def delete_stale(access_token, connection_id, external_ids):
        for key in [key for key in evidence if key not in external_ids]:
            del evidence[key]

    repository.upsert_documents = upsert_documents  # type: ignore[method-assign]
    repository.document_states = document_states  # type: ignore[method-assign]
    repository.upsert_evidence_documents = upsert_evidence_documents  # type: ignore[method-assign]
    repository.delete_stale_connector_evidence = delete_stale  # type: ignore[method-assign]
    return repository


def run_sync(repository, handler):
    service = GitHubAppSyncService(
        repository, app_id="123", private_key="unused", transport=httpx.MockTransport(handler)  # type: ignore[arg-type]
    )

    async def go():
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            return await service.sync("token", CONNECTION, limit=5)

    return asyncio.run(go())


def test_sync_indexes_a_large_file_as_parts_and_keeps_them_while_unchanged() -> None:
    text = big_source()
    repository = stateful_repository()
    files = {"services/app.py": (text.encode(), "text/x-python"), "README.md": (b"# Readme\n", "text/markdown")}

    first = run_sync(repository, repo_handler(files))
    part_ids = sorted(key for key in repository.evidence if "#part-" in key)

    assert first.ingested == 2 and first.not_indexed == 0
    assert len(part_ids) == len(split_parts(text)) > 1
    assert "acme/docs/services/app.py" not in repository.evidence
    assert all(batch <= 50 for batch in repository.evidence_calls)

    second = run_sync(repository, repo_handler(files))
    assert second.unchanged == 2 and second.ingested == 0
    assert sorted(key for key in repository.evidence if "#part-" in key) == part_ids


def test_a_shrunk_file_drops_its_obsolete_parts() -> None:
    repository = stateful_repository()
    run_sync(repository, repo_handler({"services/app.py": (big_source().encode(), "text/x-python")}))
    assert any("#part-" in key for key in repository.evidence)

    run_sync(repository, repo_handler({"services/app.py": (b"def small():\n    return 1\n", "text/x-python")}))

    assert sorted(repository.evidence) == ["acme/docs/services/app.py"]


def test_files_that_cannot_be_indexed_are_recorded_without_content_and_not_refetched() -> None:
    repository = stateful_repository()
    calls: list[str] = []
    files = {
        "data/huge.json": (b"{}", "application/json"),
        "docs/logo.png": (b"\x89PNG\r\n", "image/png"),
        "docs/notes.md": (b"   \n\n", "text/markdown"),
        "src/flaky.py": (b"", "error"),
        "src/ok.py": (b"print('ok')\n", "text/x-python"),
    }
    handler = repo_handler(files, sizes={"data/huge.json": 3 * 1024 * 1024}, calls=calls)

    first = run_sync(repository, handler)

    reasons = {key.split("/", 2)[2]: not_indexed_reason(value["mime_type"]) for key, value in repository.stored.items()}
    assert reasons == {
        "data/huge.json": "too_large",
        "docs/logo.png": "binary",
        "docs/notes.md": "empty",
        "src/flaky.py": "fetch_error",
        "src/ok.py": None,
    }
    for key, document in repository.stored.items():
        if reasons[key.split("/", 2)[2]] is not None:
            assert document["content_text"] is None and document["content_base64"] == ""
    assert "data/huge.json" not in calls
    assert first.ingested == 1 and first.not_indexed == 4
    # Logs: truncated hashes and reason codes only.
    assert set(first.not_indexed_files) == {
        (log_path_hash(CONNECTION, path), reason)
        for path, reason in reasons.items() if reason is not None
    }
    assert all(re.fullmatch(r"[0-9a-f]{16}", path_hash) for path_hash, _ in first.not_indexed_files)
    assert "huge" not in json.dumps(first.not_indexed_files)

    calls.clear()
    second = run_sync(repository, handler)
    assert calls == []
    assert second.not_indexed == 0 and second.unchanged == 5


def test_a_text_file_that_becomes_binary_loses_its_evidence() -> None:
    repository = stateful_repository()
    run_sync(repository, repo_handler({"docs/a.md": (b"# Title\nbody\n", "text/markdown")}))
    assert "acme/docs/docs/a.md" in repository.evidence

    run_sync(repository, repo_handler({"docs/a.md": (b"\x00\x01binary", "application/octet-stream")}))

    assert "acme/docs/docs/a.md" not in repository.evidence
    assert not_indexed_reason(repository.stored["acme/docs/docs/a.md"]["mime_type"]) == "binary"


# --- owner listing ---------------------------------------------------------------


def test_owner_listing_uses_the_callers_token_and_maps_reasons() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[
            {"external_id": "acme/docs/data/huge.json",
             "mime_type": "application/x-evidence-not-indexed; reason=too_large",
             "size_bytes": 3145728, "content_text": None},
            {"external_id": "acme/docs/docs/logo.png", "mime_type": "image/png",
             "size_bytes": 10, "content_text": None},
            {"external_id": "acme/docs/empty.txt", "mime_type": "text/plain",
             "size_bytes": 0, "content_text": ""},
        ])

    settings = SupabaseSettings(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
    )
    repository = ConnectorRepository(settings, transport=httpx.MockTransport(handler))
    rows = asyncio.run(repository.list_not_indexed("user-session-token", CONNECTION))

    assert rows == [
        {"repository": "acme/docs", "path": "data/huge.json", "reason": "too_large", "size_bytes": 3145728},
        {"repository": "acme/docs", "path": "docs/logo.png", "reason": "binary", "size_bytes": 10},
        {"repository": "acme/docs", "path": "empty.txt", "reason": "empty", "size_bytes": 0},
    ]
    assert seen[0].headers["authorization"] == "Bearer user-session-token"
    assert seen[0].url.params["connection_id"] == f"eq.{CONNECTION}"
    assert seen[0].url.params["or"] == "(content_text.is.null,content_text.eq.)"


@pytest.mark.parametrize("label", ["GitHub: a/b/c.py", "", None, "GitHub: a/b/c.py #part=x/2 chars=1-2"])
def test_non_part_labels_are_not_parsed_as_parts(label) -> None:
    assert parse_part_label(label) is None


# --- endpoint --------------------------------------------------------------------


def test_not_indexed_endpoint_returns_the_owner_rows_and_requires_auth() -> None:
    from fastapi.testclient import TestClient

    from evidence_service.app import create_app
    from evidence_service.auth import Principal
    from evidence_service.connector_repository import ConnectorUnavailable

    subject = UUID("11111111-1111-4111-8111-111111111111")

    class Verifier:
        async def verify(self, token: str) -> Principal:
            assert token == "verified-token"
            return Principal(issuer="https://project.supabase.co/auth/v1", subject=subject, assurance_level="aal2")

    class Lifecycle:
        async def read_state(self, subject_id, access_token):
            return "available"

    class Fence:
        async def is_open(self) -> bool:
            return True

    class Repository:
        fail = False

        async def list_not_indexed(self, access_token, connection_id):
            assert access_token == "verified-token"
            if self.fail:
                raise ConnectorUnavailable("synthetic outage")
            return [{"repository": "acme/docs", "path": "data/huge.json", "reason": "too_large", "size_bytes": 3}]

    settings = SupabaseSettings(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
    )
    repository = Repository()
    client = TestClient(create_app(
        settings,
        token_verifier=Verifier(),
        account_lifecycle_reader=Lifecycle(),
        recovery_fence_reader=Fence(),
        connector_repository=repository,  # type: ignore[arg-type]
    ))
    path = f"/v1/connectors/{CONNECTION}/not-indexed"

    assert client.get(path).status_code == 401
    response = client.get(path, headers={"Authorization": "Bearer verified-token"})
    assert response.status_code == 200
    assert response.json() == [
        {"repository": "acme/docs", "path": "data/huge.json", "reason": "too_large", "size_bytes": 3}
    ]
    repository.fail = True
    assert client.get(path, headers={"Authorization": "Bearer verified-token"}).status_code == 503
