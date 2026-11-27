"""Exact-identifier boost: code that defines a queried identifier is not
outranked by documentation that describes it (lexical and JEV paths)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from evidence_service.chunking import (
    IDENTIFIER_BOOST_MANY,
    IDENTIFIER_BOOST_ONE,
    identifier_boost,
    is_code_path,
    query_identifiers,
)
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.github_app_sync_service import GitHubAppSyncService
from evidence_service.retrieval_service import select_evidence

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = UUID("44444444-4444-4444-8444-444444444444")
NATURAL_QUERY = "FastAPI route list_not_indexed_connector_files ConnectorNotIndexedFile in evidence service app.py"
# The documentation that outranked app.py in production (2026-11-26) plus
# other files that mention the same terms.
COMPETITORS = (
    "services/evidence-service/evidence_service/app.py",
    "docs/connectors/README.md",
    "docs/connectors/api-retrieval-mcp.md",
    "docs/IMPLEMENTATION-AUDIT-CURRENT.md",
    "docs/connectors/github-sync-and-retrieve.md",
    "apps/web/tests/connector-sources.test.tsx",
    "apps/web/components/connector-sources.tsx",
    "services/evidence-service/evidence_service/connector_repository.py",
    "services/evidence-service/evidence_service/__init__.py",
)


def repository_records(paths=COMPETITORS) -> list[EvidenceRecord]:
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    records = []
    for path in paths:
        text = (ROOT / path).read_text(encoding="utf-8")
        for record in GitHubAppSyncService._evidence_records(f"acme/repo/{path}", text, "rev"):
            records.append(EvidenceRecord(
                id=uuid5(WORKSPACE, record["connector_external_id"]), workspace_id=WORKSPACE,
                content=record["content"], source_label=record["source_label"],
                created_at=now, updated_at=now,
            ))
    return records


def defines_route(item) -> bool:
    return "def list_not_indexed_connector_files" in item.content


def test_natural_language_query_retrieves_the_app_py_part_that_defines_the_route() -> None:
    result = select_evidence(
        repository_records(), query=NATURAL_QUERY, top_k=5, max_tokens=1200, max_chars=6000,
        identifier_boost_enabled=True,
    )

    first = result.selected[0]
    assert first.path == "services/evidence-service/evidence_service/app.py"
    assert defines_route(first)
    assert "#part=" in first.record.source_label  # app.py is indexed in parts
    assert first.boost == IDENTIFIER_BOOST_MANY
    assert first.boosted_score == pytest.approx(first.score * IDENTIFIER_BOOST_MANY)


def test_with_the_flag_off_ranking_is_unchanged_and_nothing_is_boosted() -> None:
    records = repository_records()
    off = select_evidence(records, query=NATURAL_QUERY, top_k=5, max_tokens=1200, max_chars=6000)

    assert not defines_route(off.selected[0])  # the gap the boost addresses
    assert all(item.boost == 1.0 and item.boosted_score == item.score for item in off.selected)


def test_on_the_jev_path_the_defining_code_beats_prose_about_it() -> None:
    records = repository_records()
    lexical = select_evidence(
        records, query=NATURAL_QUERY, top_k=50, max_tokens=50_000, max_chars=200_000,
        identifier_boost_enabled=True,
    )
    # A provider that, as in production, prefers the README prose (0.81)
    # over the code (0.50).
    overrides = {
        item.chunk_id: (0.81 if item.path == "docs/connectors/README.md" else 0.50 if defines_route(item) else 0.30)
        for item in lexical.selected
    }
    assert any(defines_route(item) for item in lexical.selected), "the code chunk must reach the JEV batch"

    result = select_evidence(
        records, query=NATURAL_QUERY, top_k=5, max_tokens=1200, max_chars=6000, score_overrides=overrides,
        identifier_boost_enabled=True,
    )

    first = result.selected[0]
    assert defines_route(first)
    assert first.score == 0.50                       # the provider score, unchanged, in [0, 1]
    assert first.boosted_score == pytest.approx(0.50 * IDENTIFIER_BOOST_MANY)
    assert all(0 <= item.score <= 1 for item in result.selected)


def test_queries_without_identifiers_rank_exactly_as_before() -> None:
    records = repository_records()
    query = "How are files that are not indexed shown to the owner?"
    assert query_identifiers(query) == frozenset()

    result = select_evidence(records, query=query, top_k=5, max_tokens=1200, max_chars=6000)
    boosted = select_evidence(
        records, query=query, top_k=5, max_tokens=1200, max_chars=6000, identifier_boost_enabled=True,
    )
    assert [item.chunk_id for item in boosted.selected] == [item.chunk_id for item in result.selected]
    overrides = {item.chunk_id: 0.5 for item in result.selected}
    jev = select_evidence(
        records, query=query, top_k=5, max_tokens=1200, max_chars=6000, score_overrides=overrides,
        identifier_boost_enabled=True,
    )

    assert all(item.score == 0.5 for item in jev.selected)


@pytest.mark.parametrize(("query", "expected"), [
    (NATURAL_QUERY, {"list_not_indexed_connector_files", "ConnectorNotIndexedFile"}),
    ("Remote MCP retrieve_evidence tool", {"retrieve_evidence"}),
    ("where is getVerifiedAuthContext called", {"getVerifiedAuthContext"}),
    ("How are large GitHub files split into parts?", set()),
    ("JavaScript TypeScript PostgreSQL FastAPI", set()),
    ("SupabaseSettings config", set()),  # two segments: not distinctive enough
    ("a_b x_y", set()),                   # shorter than 6 characters
])
def test_identifier_extraction(query: str, expected: set[str]) -> None:
    assert query_identifiers(query) == frozenset(expected)


def test_boost_applies_to_code_only_and_needs_an_exact_whole_identifier() -> None:
    ids = frozenset({"list_not_indexed_connector_files", "ConnectorNotIndexedFile"})
    code = "async def list_not_indexed_connector_files(...) -> list[ConnectorNotIndexedFile]:"

    assert identifier_boost(ids, path="services/app.py", content=code) == IDENTIFIER_BOOST_MANY
    assert identifier_boost(ids, path="services/app.py", content="list_not_indexed_connector_files()") == IDENTIFIER_BOOST_ONE
    assert identifier_boost(ids, path="docs/README.md", content=code) == 1.0      # docs are never boosted or penalised
    assert identifier_boost(ids, path="services/app.py", content="list_not_indexed_connector_files_v2") == 1.0
    assert identifier_boost(ids, path="services/app.py", content="connectornotindexedfile") == 1.0  # case-sensitive
    assert identifier_boost(frozenset(), path="services/app.py", content=code) == 1.0
    assert is_code_path("a/b.tsx") and is_code_path("x.sql") and not is_code_path("README") and not is_code_path(None)


def test_the_api_reports_score_unchanged_and_the_boost_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_evidence_service_retrieval import TOKEN, FakeRepository, make_client
    from evidence_service.app import create_app

    class RepoRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.records = [
                EvidenceRecord(id=r.id, workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
                               content=r.content, source_label=r.source_label,
                               created_at=r.created_at, updated_at=r.updated_at)
                for r in repository_records()
            ]

    def run(enabled: bool | None):
        monkeypatch.delenv("EVIDENCE_IDENTIFIER_BOOST_ENABLED", raising=False)
        client = make_client(repository=RepoRepository())
        if enabled is not None:
            client.app.state.identifier_boost_enabled = enabled
        response = client.post(
            "/v1/personal-api-keys/workspaces/22222222-2222-4222-8222-222222222222/retrieve",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"query": NATURAL_QUERY, "top_k": 3, "use_jev": False},
        )
        assert response.status_code == 200
        return response.json()["evidence"]

    default = run(None)
    assert all(item["boost"] == 1.0 and item["boosted_score"] == item["score"] for item in default)
    assert "def list_not_indexed_connector_files" not in default[0]["content"]  # default off

    boosted = run(True)
    assert "def list_not_indexed_connector_files" in boosted[0]["content"]
    assert boosted[0]["boost"] == IDENTIFIER_BOOST_MANY
    assert boosted[0]["boosted_score"] == pytest.approx(boosted[0]["score"] * IDENTIFIER_BOOST_MANY)
