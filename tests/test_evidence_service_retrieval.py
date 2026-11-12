from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from evidence_service.app import create_app
from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.personal_api_key_repository import PersonalApiKeyPage

WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
KEY_ID = UUID("66666666-6666-4666-8666-666666666666")
RECORD_ID = UUID("11111111-1111-4111-8111-111111111111")
TOKEN = f"evl_pk_{KEY_ID}.{'A' * 43}"


class FakeRepository:
    def __init__(self):
        self.record = EvidenceRecord(
            id=RECORD_ID,
            workspace_id=WORKSPACE,
            content="lexical retrieval evidence",
            source_label="Source",
            created_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
            updated_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
        )

    async def read_evidence(self, **kwargs):
        return PersonalApiKeyPage(items=[self.record], next_cursor=None)

    async def read_evidence_document(self, **kwargs):
        return self.record if kwargs["evidence_id"] == RECORD_ID else None


class OpenFence:
    async def is_open(self) -> bool:
        return True


def make_client(enabled: bool = True):
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
        personal_api_key_repository=FakeRepository(),
        retrieval_enabled=enabled,
    )
    return TestClient(app)


def test_retrieve_with_personal_api_key():
    client = make_client()
    response = client.post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "lexical retrieval", "top_k": 1, "max_tokens": 100, "max_chars": 1000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["selected_count"] == 1
    assert body["evidence"][0]["evidence_id"] == str(RECORD_ID)


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
