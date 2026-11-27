"""Step E: retrieval candidates cover the whole workspace (20261127120000).

The regression: an old record outside the 1,000 newest must be retrievable
through API-key retrieval, Remote MCP and the signed-in task-context, while a
database without the migration keeps today's newest-window behaviour.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import (
    EvidenceDataUnavailable,
    EvidenceRecord,
    EvidenceSearchUnavailable,
    SupabaseEvidenceRepository,
)
from evidence_service.personal_api_key_repository import (
    PersonalApiKeyDataUnavailable,
    PersonalApiKeyPage,
    PersonalApiKeySearchUnavailable,
    SupabasePersonalApiKeyRepository,
    gateway_signature,
)
from test_evidence_service_relationships import (
    SUBJECT,
    WORKSPACE_A,
    MembershipDirectory,
    RelationshipRepositorySpy,
)
from test_evidence_service_relationships import TOKEN as SESSION_TOKEN
from test_evidence_service_relationships import make_client as make_session_client
from test_evidence_service_retrieval import TOKEN, WORKSPACE, FakeRepository, make_client

OLD_ID = UUID("0a000000-0000-4000-8000-000000000001")
QUERY = "How does the zebrafish ledger reconciliation work?"
SECRET = "ab" * 32


def workspace_records(workspace_id: UUID) -> tuple[list[EvidenceRecord], EvidenceRecord]:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    newest = [
        EvidenceRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            content=f"Routine status note number {n} about deployments and builds.",
            source_label=f"GitHub: acme/docs/notes/status-{n}.md",
            created_at=start + timedelta(minutes=n),
            updated_at=start + timedelta(minutes=n),
        )
        for n in range(1000, 0, -1)
    ]
    old = EvidenceRecord(
        id=OLD_ID,
        workspace_id=workspace_id,
        content="Zebrafish ledger reconciliation runs nightly and closes the books.",
        source_label="GitHub: acme/docs/finance/ledger.md",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return newest, old


class WholeWorkspaceRepository(FakeRepository):
    """API-key fake honouring the RPC contract: newest ``limit`` rows, plus
    full-text matches outside them when ``search_limit`` > 0."""

    def __init__(self, *, migrated: bool = True, size: int = 1000):
        super().__init__()
        newest, self.old = workspace_records(WORKSPACE)
        self.newest = newest[:size]
        self.migrated = migrated
        self.candidate_calls: list[dict] = []
        self.newest_calls: list[int] = []

    @property
    def workspace(self) -> list[EvidenceRecord]:
        return [*self.newest, self.old]  # newest first; the answer is the oldest

    async def read_evidence(self, **kwargs):
        self.newest_calls.append(kwargs["limit"])
        return PersonalApiKeyPage(items=self.workspace[: kwargs["limit"]], next_cursor=None)

    async def read_evidence_candidates(self, **kwargs):
        self.candidate_calls.append(kwargs)
        if not self.migrated:
            raise PersonalApiKeySearchUnavailable
        window = self.workspace[: kwargs["limit"]]
        matches = [self.old] if kwargs.get("search_limit", 200) > 0 and self.old not in window else []
        return [*window, *matches]


def retrieve(client, query: str = QUERY):
    return client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": query, "top_k": 3, "max_tokens": 400, "max_chars": 2000},
    )


# --- API-key retrieval ------------------------------------------------------------


def test_api_retrieval_reaches_a_record_outside_the_1000_newest() -> None:
    repository = WholeWorkspaceRepository()  # 1000 newer records + the old answer
    assert OLD_ID not in {record.id for record in repository.newest}

    response = retrieve(make_client(repository=repository))

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"][0]["evidence_id"] == str(OLD_ID)
    assert body["candidate_count"] == 1001
    # Search off first; the extra row shows the window is full, so search runs.
    assert [(call["limit"], call.get("search_limit", 200)) for call in repository.candidate_calls] == [
        (1001, 0), (1000, 200),
    ]
    assert repository.candidate_calls[1]["query"] == QUERY
    assert repository.newest_calls == []


def test_a_workspace_inside_the_window_costs_one_call_without_search() -> None:
    repository = WholeWorkspaceRepository(size=10)

    response = retrieve(make_client(repository=repository))

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == 11  # the whole workspace
    assert body["evidence"][0]["evidence_id"] == str(OLD_ID)
    assert [(call["limit"], call["search_limit"]) for call in repository.candidate_calls] == [(1001, 0)]
    assert repository.newest_calls == []


def test_before_the_migration_api_retrieval_keeps_the_newest_window() -> None:
    repository = WholeWorkspaceRepository(migrated=False)

    response = retrieve(make_client(repository=repository))

    assert response.status_code == 200
    body = response.json()
    assert str(OLD_ID) not in {item["evidence_id"] for item in body["evidence"]}
    assert body["candidate_count"] == 1000
    assert repository.newest_calls == [1000]


def test_remote_mcp_retrieval_reaches_the_old_record() -> None:
    repository = WholeWorkspaceRepository()
    with make_client(remote_mcp_enabled=True, repository=repository) as client:
        headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"}
        client.post(f"/mcp/workspaces/{WORKSPACE}/", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        called = client.post(f"/mcp/workspaces/{WORKSPACE}/", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "retrieve_evidence", "arguments": {"query": QUERY, "top_k": 3}},
        })

    assert called.status_code == 200
    result = called.json()["result"]
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["evidence"][0]["evidence_id"] == str(OLD_ID)
    assert [call.get("search_limit", 200) for call in repository.candidate_calls] == [0, 200]


# --- signed-in task-context ---------------------------------------------------------


class SessionEvidence:
    def __init__(self, *, migrated: bool = True, size: int = 1000):
        newest, self.old = workspace_records(WORKSPACE_A)
        self.newest = newest[:size]
        self.migrated = migrated
        self.searches: list[tuple[str, UUID, str]] = []

    async def list_records(self, access_token, workspace_id):
        # Capped like the Data API: the oldest record is beyond 1000 rows.
        return [*self.newest, self.old][:1000]

    async def search_records(self, access_token, workspace_id, query):
        self.searches.append((access_token, workspace_id, query))
        if not self.migrated:
            raise EvidenceSearchUnavailable
        return [self.newest[0], self.old]  # duplicates are merged


def task_context(evidence) -> dict:
    async def run():
        directory = MembershipDirectory()
        directory.roles[(SUBJECT, WORKSPACE_A)] = "owner"
        async with make_session_client(
            directory, RelationshipRepositorySpy(), evidence_repository=evidence, retrieval_enabled=True,
        ) as client:
            response = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/task-context",
                headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
                json={"task": QUERY, "expected_outcome": "explain the reconciliation",
                      "top_k": 1, "max_tokens": 400, "max_chars": 1600},
            )
        assert response.status_code == 200
        return response.json()

    return asyncio.run(run())


def test_task_context_reaches_a_record_outside_the_1000_newest() -> None:
    evidence = SessionEvidence()

    body = task_context(evidence)

    assert body["retrieval"]["evidence"][0]["evidence_id"] == str(OLD_ID)
    assert body["retrieval"]["candidate_count"] == 1001
    token, workspace_id, query = evidence.searches[0]
    assert (token, workspace_id) == (SESSION_TOKEN, WORKSPACE_A)
    assert QUERY in query


def test_task_context_skips_the_search_when_the_workspace_fits_the_window() -> None:
    evidence = SessionEvidence(size=10)

    body = task_context(evidence)

    assert evidence.searches == []
    assert body["retrieval"]["candidate_count"] == 11
    assert body["retrieval"]["evidence"][0]["evidence_id"] == str(OLD_ID)


def test_before_the_migration_task_context_keeps_the_newest_records() -> None:
    body = task_context(SessionEvidence(migrated=False))

    assert body["retrieval"]["candidate_count"] == 1000
    assert str(OLD_ID) not in {item["evidence_id"] for item in body["retrieval"]["evidence"]}


# --- repositories ---------------------------------------------------------------------


def settings(**overrides) -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
        **overrides,
    )


def row(record_id: UUID, workspace_id: UUID) -> dict:
    return {
        "id": str(record_id), "workspace_id": str(workspace_id), "content": "Zebrafish ledger",
        "source_label": "ledger.md", "source_id": None, "source_version_id": None,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    }


MISSING_FUNCTION = httpx.Response(404, json={"code": "PGRST202", "message": "Could not find the function"})


def test_signed_candidate_read_reuses_the_unchanged_evidence_proof() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[row(OLD_ID, WORKSPACE)])

    repository = SupabasePersonalApiKeyRepository(
        settings(personal_api_key_gateway_secret=SECRET), transport=httpx.MockTransport(handler)
    )
    records = asyncio.run(repository.read_evidence_candidates(
        key_id=UUID("66666666-6666-4666-8666-666666666666"), key_hash="c" * 64,
        workspace_id=WORKSPACE, limit=1000, query=QUERY,
    ))

    assert [record.id for record in records] == [OLD_ID]
    request = seen[0]
    assert request.url.path == "/rest/v1/rpc/read_personal_api_key_evidence_candidates"
    body = json.loads(request.content)
    assert body == {
        "p_key_id": "66666666-6666-4666-8666-666666666666", "p_workspace_id": str(WORKSPACE),
        "p_limit": 1000, "p_query": QUERY, "p_search_limit": 200,
    }
    assert request.headers["x-evidence-api-key-gateway-proof"] == gateway_signature(
        SECRET, resource="evidence", key_id=UUID("66666666-6666-4666-8666-666666666666"),
        workspace_id=WORKSPACE, key_hash="c" * 64,
        timestamp=int(request.headers["x-evidence-api-key-gateway-timestamp"]),
        nonce=UUID(request.headers["x-evidence-api-key-gateway-nonce"]),
        limit=1000, cursor_created_at=None, cursor_id=None,
    )


def test_a_database_without_the_migration_is_reported_as_search_unavailable() -> None:
    api = SupabasePersonalApiKeyRepository(
        settings(personal_api_key_gateway_secret=SECRET),
        transport=httpx.MockTransport(lambda request: MISSING_FUNCTION),
    )
    with pytest.raises(PersonalApiKeySearchUnavailable) as raised:
        asyncio.run(api.read_evidence_candidates(
            key_id=uuid4(), key_hash="c" * 64, workspace_id=WORKSPACE, limit=1000, query=QUERY,
        ))
    # Other callers keep treating it as an ordinary data outage.
    assert isinstance(raised.value, PersonalApiKeyDataUnavailable)

    session = SupabaseEvidenceRepository(settings(), transport=httpx.MockTransport(lambda request: MISSING_FUNCTION))
    with pytest.raises(EvidenceSearchUnavailable) as raised_session:
        asyncio.run(session.search_records("user-token", WORKSPACE_A, QUERY))
    assert isinstance(raised_session.value, EvidenceDataUnavailable)


def test_session_search_uses_the_callers_token_and_bounds_the_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[row(OLD_ID, WORKSPACE_A)])

    repository = SupabaseEvidenceRepository(settings(), transport=httpx.MockTransport(handler))
    records = asyncio.run(repository.search_records("user-token", WORKSPACE_A, "z" * 5000))

    assert [record.id for record in records] == [OLD_ID]
    assert seen[0].url.path == "/rest/v1/rpc/search_workspace_evidence"
    assert seen[0].headers["authorization"] == "Bearer user-token"
    body = json.loads(seen[0].content)
    assert body["p_workspace_id"] == str(WORKSPACE_A) and body["p_limit"] == 200
    assert len(body["p_query"]) == 2000


def test_session_search_rejects_rows_from_another_workspace() -> None:
    repository = SupabaseEvidenceRepository(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[row(OLD_ID, WORKSPACE)])
    ))
    with pytest.raises(EvidenceDataUnavailable):
        asyncio.run(repository.search_records("user-token", WORKSPACE_A, QUERY))
