from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.connector_repository import ConnectorRepository

SETTINGS = SupabaseSettings(
    supabase_url="https://project.supabase.co",
    publishable_key="sb_publishable_test",
    jwt_issuer="https://project.supabase.co/auth/v1",
    jwt_audience="authenticated",
    jwt_algorithms=("ES256",),
)
USER = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION = UUID("22222222-2222-4222-8222-222222222222")


def _row(include_secrets: bool = False) -> dict:
    row = {
        "connection_id": str(CONNECTION),
        "user_id": str(USER),
        "provider": "google_drive",
        "account_email": "owner@example.test",
        "scopes": ["drive.readonly"],
        "status": "connected",
        "last_sync_at": None,
        "last_error_category": None,
        "access_token_expires_at": "2026-11-11T00:00:00Z",
        "created_at": "2026-11-11T00:00:00Z",
        "updated_at": "2026-11-11T00:00:00Z",
    }
    if include_secrets:
        row["access_token_ciphertext"] = "v1.access"
        row["refresh_token_ciphertext"] = "v1.refresh"
    return row


class ConnectorRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_get_list_and_documents(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("prefer")))
            if request.method == "POST" and request.url.path.endswith("/connector_connections"):
                body = json.loads(request.content)
                assert body["user_id"] == str(USER)
                assert body["access_token_ciphertext"] == "v1.access"
                return httpx.Response(201, json=[_row()])
            if request.method == "GET" and request.url.path.endswith("/connector_connections"):
                if "connection_id" in request.url.params or "provider" in request.url.params:
                    return httpx.Response(200, json=[_row(include_secrets=True)])
                return httpx.Response(200, json=[_row()])
            if request.method == "GET" and request.url.path.endswith("/workspace_memberships"):
                return httpx.Response(200, json=[{
                    "workspace_id": str(WORKSPACE),
                    "role": "owner",
                    "status": "active",
                    "workspaces": {"id": str(WORKSPACE), "name": "Personal", "created_at": "2026-11-11T00:00:00Z"},
                }])
            if request.method == "POST" and request.url.path.endswith("/rpc/upsert_connector_evidence"):
                body = json.loads(request.content)
                assert body["p_documents"][0]["connector_external_id"] == "file-1"
                return httpx.Response(200, json=1)
            if request.method == "POST" and request.url.path.endswith("/connector_documents"):
                body = json.loads(request.content)
                assert body[0]["content_text"] == "hello"
                return httpx.Response(201)
            return httpx.Response(404)

        repository = ConnectorRepository(SETTINGS, transport=httpx.MockTransport(handler))
        created = await repository.upsert_connection(
            "token",
            user_id=USER,
            provider="google_drive",
            account_email="owner@example.test",
            scopes=["drive.readonly"],
            access_token_ciphertext="v1.access",
            refresh_token_ciphertext="v1.refresh",
            access_token_expires_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        self.assertEqual(created.connection_id, CONNECTION)
        listed = await repository.list_connections("token")
        self.assertEqual(len(listed), 1)
        loaded = await repository.get_connection("token", CONNECTION)
        self.assertEqual(loaded.access_token_ciphertext, "v1.access")
        by_provider = await repository.get_connection_for_provider("token", "google_drive")
        assert by_provider is not None
        self.assertEqual(by_provider.refresh_token_ciphertext, "v1.refresh")
        self.assertEqual(await repository.personal_workspace_id("token", USER), WORKSPACE)
        await repository.upsert_evidence_documents(
            "token",
            workspace_id=WORKSPACE,
            connection_id=CONNECTION,
            documents=[{
                "content": "hello",
                "source_label": "GitHub: file-1",
                "connector_external_id": "file-1",
                "connector_revision": "rev",
            }],
        )
        await repository.upsert_documents("token", [{
            "user_id": str(USER),
            "connection_id": str(CONNECTION),
            "provider": "google_drive",
            "external_id": "file-1",
            "name": "File",
            "revision": "rev",
            "content_text": "hello",
            "content_sha256": "a" * 64,
            "size_bytes": 5,
        }])
        self.assertTrue(any(method == "POST" for method, _, _ in seen))
