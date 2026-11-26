from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from evidence_service.app import create_app
from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.personal_api_key_repository import PersonalApiKeyPage
from evidence_service.relationship_repository import EvidenceRelationship

WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
KEY_ID = UUID("66666666-6666-4666-8666-666666666666")
RECORD_ID = UUID("11111111-1111-4111-8111-111111111111")
TOKEN = f"evl_pk_{KEY_ID}.{'A' * 43}"


class FakeRepository:
    def __init__(self):
        created_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.record = EvidenceRecord(
            id=RECORD_ID,
            workspace_id=WORKSPACE,
            content="lexical retrieval evidence",
            source_label="Source",
            created_at=created_at,
            updated_at=created_at,
        )
        self.records = [self.record]
        self.relationships: list[EvidenceRelationship] = []
        self.relationship_reads = 0

    async def read_evidence(self, **kwargs):
        return PersonalApiKeyPage(items=self.records, next_cursor=None)

    async def read_evidence_document(self, **kwargs):
        return self.record if kwargs["evidence_id"] == RECORD_ID else None

    async def read_relationships(self, **kwargs):
        self.relationship_reads += 1
        return PersonalApiKeyPage(items=self.relationships, next_cursor=None)


class RelatedRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        related_id = UUID("33333333-3333-4333-8333-333333333333")
        unrelated_id = UUID("44444444-4444-4444-8444-444444444444")
        created_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.records.extend([
            EvidenceRecord(
                id=related_id,
                workspace_id=WORKSPACE,
                content="Recovery after failed deployment.",
                source_label="rollback.md",
                created_at=created_at,
                updated_at=created_at,
            ),
            EvidenceRecord(
                id=unrelated_id,
                workspace_id=WORKSPACE,
                content="Unrelated cooking notes.",
                source_label="notes.md",
                created_at=created_at,
                updated_at=created_at,
            ),
        ])
        self.relationships = [
            EvidenceRelationship(
                id=UUID("55555555-5555-4555-8555-555555555555"),
                workspace_id=WORKSPACE,
                evidence_id_a=RECORD_ID,
                evidence_id_b=related_id,
                relation_type="supports",
                rationale="Rollback documents support the release procedure.",
                provenance_note="Manually reviewed both records.",
                created_by=UUID("66666666-6666-4666-8666-666666666666"),
                created_at=created_at,
                updated_by=UUID("66666666-6666-4666-8666-666666666666"),
                updated_at=created_at,
            )
        ]

class LongRepository(FakeRepository):
    def __init__(self):
        created_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.record = EvidenceRecord(
            id=RECORD_ID,
            workspace_id=WORKSPACE,
            content="# Evidence Layer\n\n" + ("lexical retrieval product evidence " * 800),
            source_label="GitHub: owner/repo/README.md",
            created_at=created_at,
            updated_at=created_at,
        )
        self.records = [self.record]
        self.relationships = []


class TwoMatchRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        created_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.records.extend([
            EvidenceRecord(
                id=UUID("77777777-7777-4777-8777-777777777777"),
                workspace_id=WORKSPACE,
                content="lexical retrieval secondary evidence",
                source_label="second.md",
                created_at=created_at,
                updated_at=created_at,
            ),
        ])


class FakeJevScorer:
    def __init__(self, *, failure: Exception | None = None):
        self.calls = []
        self.failure = failure

    def score_candidates(self, query, candidates, *, transfer_permit):
        self.calls.append((query, candidates, transfer_permit))
        if self.failure:
            raise self.failure
        return tuple(
            0.9 if "secondary" in item.text else 0.1 for item in candidates
        )


class OpenFence:
    async def is_open(self) -> bool:
        return True


def make_client(
    enabled: bool = True,
    repository=None,
    remote_mcp_enabled: bool = False,
    jev_enabled: bool = False,
    jev_scorer=None,
):
    settings = SupabaseSettings(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
    )
    app = create_app(
        settings,
        recovery_fence_reader=OpenFence(),
        personal_api_key_repository=repository or FakeRepository(),
        retrieval_enabled=enabled,
        jev_enabled=jev_enabled,
        jev_scorer=jev_scorer,
        remote_mcp_enabled=remote_mcp_enabled,
    )
    return TestClient(app)


def test_retrieve_with_personal_api_key():
    repository = FakeRepository()
    client = make_client(repository=repository)
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "lexical retrieval", "top_k": 1, "max_tokens": 100, "max_chars": 1000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["selected_count"] == 1
    assert body["chunking"] == "retrieve_time_v1"
    assert body["chunk_count"] == 1
    assert body["matched_chunk_count"] == 1
    item = body["evidence"][0]
    assert item["evidence_id"] == str(RECORD_ID)
    assert item["chunk_id"].startswith(f"{RECORD_ID}:")
    assert item["chunk_start"] == 0
    assert item["chunk_end"] == len("lexical retrieval evidence")
    assert body["relationships"] == []
    assert body["scoring_provider"] == "lexical_stopword_filtered"
    assert body["scoring_fallback_reason"] == "jev_disabled"
    assert repository.relationship_reads == 0


def test_jev_requires_server_gate_and_explicit_request_opt_in():
    scorer = FakeJevScorer()
    client = make_client(repository=TwoMatchRepository(), jev_scorer=scorer)
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "query": "lexical retrieval",
            "top_k": 1,
            "max_tokens": 100,
            "max_chars": 1000,
            "use_jev": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["scoring_provider"] == "lexical_stopword_filtered"
    assert response.json()["scoring_fallback_reason"] == "jev_disabled"
    assert scorer.calls == []


def test_jev_defaults_to_primary_when_enabled_and_false_forces_lexical():
    scorer = FakeJevScorer()
    client = make_client(
        repository=TwoMatchRepository(),
        jev_enabled=True,
        jev_scorer=scorer,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    path = f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve"

    invalid = client.post(
        path,
        headers=headers,
        json={"query": "lexical retrieval", "use_jev": "true"},
    )
    assert invalid.status_code == 422
    assert scorer.calls == []

    ordinary = client.post(
        path,
        headers=headers,
        json={"query": "lexical retrieval"},
    )
    assert ordinary.status_code == 200
    assert ordinary.json()["scoring_provider"] == "jev"
    assert scorer.calls

    lexical = client.post(path, headers=headers, json={"query": "lexical retrieval", "use_jev": False})
    assert lexical.status_code == 200
    assert lexical.json()["scoring_provider"] == "lexical_stopword_filtered"
    assert len(scorer.calls) == 1


def test_jev_ranks_the_bounded_full_candidate_batch_only_after_opt_in():
    scorer = FakeJevScorer()
    client = make_client(
        repository=TwoMatchRepository(),
        jev_enabled=True,
        jev_scorer=scorer,
    )
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "query": "lexical retrieval",
            "top_k": 1,
            "max_tokens": 100,
            "max_chars": 1000,
            "use_jev": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scoring_provider"] == "jev"
    assert body["scoring_fallback_reason"] is None
    assert body["evidence"][0]["evidence_id"] == "77777777-7777-4777-8777-777777777777"
    assert len(scorer.calls) == 1
    assert len(scorer.calls[0][1]) == 2
    assert scorer.calls[0][2].tenant_id == str(WORKSPACE)


def test_jev_partial_failure_discards_scores_and_recomputes_lexically():
    scorer = FakeJevScorer(failure=RuntimeError("private provider details"))
    client = make_client(
        repository=TwoMatchRepository(),
        jev_enabled=True,
        jev_scorer=scorer,
    )
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "query": "lexical retrieval",
            "top_k": 1,
            "max_tokens": 100,
            "max_chars": 1000,
            "use_jev": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scoring_provider"] == "lexical_stopword_filtered"
    assert body["scoring_fallback_reason"] == "provider_failure"
    assert body["evidence"][0]["evidence_id"] == str(RECORD_ID)
    assert "private provider details" not in response.text


def test_personal_api_key_retrieval_expands_explicit_relationships_within_top_k():
    repository = RelatedRepository()
    client = make_client(repository=repository)
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "query": "lexical retrieval",
            "top_k": 2,
            "max_tokens": 100,
            "max_chars": 1000,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_count"] == 2
    assert [item["evidence_id"] for item in body["evidence"]] == [
        str(RECORD_ID),
        "33333333-3333-4333-8333-333333333333",
    ]
    assert body["matched_chunk_count"] == 1
    assert body["relationships"] == [
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "evidence_id_a": str(RECORD_ID),
            "evidence_id_b": "33333333-3333-4333-8333-333333333333",
            "relation_type": "supports",
            "rationale": "Rollback documents support the release procedure.",
            "provenance_note": "Manually reviewed both records.",
        }
    ]
    assert repository.relationship_reads == 1


def test_retrieve_returns_ranked_chunks_not_the_whole_document():
    client = make_client(repository=LongRepository())
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "query": "readme product evidence",
            "top_k": 1,
            "max_tokens": 1000,
            "max_chars": 4000,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] > 1
    item = body["evidence"][0]
    assert item["path"] == "README.md"
    assert item["title"] == "Evidence Layer"
    assert 0 <= item["chunk_start"] < item["chunk_end"] < len(LongRepository().record.content)
    assert len(item["content"]) == item["char_count"] < len(LongRepository().record.content)


def test_get_document_with_personal_api_key():
    client = make_client()
    response = client.get(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/evidence/{RECORD_ID}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(RECORD_ID)


def test_retrieval_is_disabled_by_default():
    client = make_client(enabled=False)
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "lexical"},
    )
    assert response.status_code == 404


def test_remote_mcp_exposes_retrieve_and_document_tools():
    repository = RelatedRepository()
    with make_client(remote_mcp_enabled=True, repository=repository) as client:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json, text/event-stream",
        }
        initialize = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert initialize.status_code == 200
        assert initialize.json()["result"]["serverInfo"]["name"] == "Evidence Layer Remote MCP"

        listed = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert listed.status_code == 200
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert names == {"retrieve_evidence", "get_document"}

        retrieved = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "retrieve_evidence",
                    "arguments": {"query": "lexical retrieval", "top_k": 2},
                },
            },
        )
        assert retrieved.status_code == 200
        payload = json.loads(retrieved.json()["result"]["content"][0]["text"])
        assert payload["status"] == "ok"
        assert payload["chunking"] == "retrieve_time_v1"
        assert payload["evidence"][0]["evidence_id"] == str(RECORD_ID)
        assert payload["evidence"][1]["evidence_id"] == "33333333-3333-4333-8333-333333333333"
        assert payload["relationships"][0]["relation_type"] == "supports"
        assert repository.relationship_reads == 1

        opted_in = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "retrieve_evidence",
                    "arguments": {
                        "query": "lexical retrieval",
                        "top_k": 2,
                        "use_jev": True,
                    },
                },
            },
        )
        assert opted_in.status_code == 200
        opted_payload = json.loads(
            opted_in.json()["result"]["content"][0]["text"]
        )
        assert opted_payload["scoring_provider"] == "lexical_stopword_filtered"
        assert opted_payload["scoring_fallback_reason"] == "jev_disabled"

        document = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_document", "arguments": {"evidence_id": str(RECORD_ID)}},
            },
        )
        assert document.status_code == 200
        document_payload = json.loads(document.json()["result"]["content"][0]["text"])
        assert document_payload["id"] == str(RECORD_ID)


def test_remote_mcp_requires_a_personal_key_and_feature_flag():
    with make_client(remote_mcp_enabled=True) as client:
        response = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    with make_client(remote_mcp_enabled=False) as client:
        response = client.post(
            f"/mcp/workspaces/{WORKSPACE}/",
            headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 404


class LimitRecordingRepository(FakeRepository):
    def __init__(self, *, max_limit: int | None = None, denied: bool = False):
        super().__init__()
        self.limits: list[int] = []
        self.max_limit = max_limit
        self.denied = denied

    async def read_evidence(self, **kwargs):
        from evidence_service.personal_api_key_repository import PersonalApiKeyAccessDenied

        self.limits.append(kwargs["limit"])
        if self.denied or (self.max_limit is not None and kwargs["limit"] > self.max_limit):
            raise PersonalApiKeyAccessDenied
        return PersonalApiKeyPage(items=self.records, next_cursor=None)


def _retrieve(client):
    return client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "lexical retrieval", "top_k": 1, "max_tokens": 100, "max_chars": 1000},
    )


def test_retrieval_ranks_a_1000_record_window_in_one_read():
    repository = LimitRecordingRepository()
    response = _retrieve(make_client(repository=repository))
    assert response.status_code == 200
    assert repository.limits == [1000]


def test_retrieval_falls_back_to_100_records_before_the_migration():
    repository = LimitRecordingRepository(max_limit=100)
    response = _retrieve(make_client(repository=repository))
    assert response.status_code == 200
    assert repository.limits == [1000, 100]


def test_retrieval_still_refuses_an_invalid_key_after_the_fallback():
    repository = LimitRecordingRepository(denied=True)
    response = _retrieve(make_client(repository=repository))
    assert response.status_code == 401
    assert repository.limits == [1000, 100]


def test_retrieval_rejects_a_window_above_1000():
    response = make_client().post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "q", "candidate_limit": 1001},
    )
    assert response.status_code == 422
