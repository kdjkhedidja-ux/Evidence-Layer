from __future__ import annotations

import base64
import unittest
from datetime import datetime, timezone
from uuid import UUID

import httpx

from evidence_service.connector_crypto import TokenVault
from evidence_service.connector_repository import ConnectorConnection
from evidence_service.notion_sync_service import NotionSyncService

USER = UUID("11111111-1111-4111-8111-111111111111")
CONNECTION = UUID("22222222-2222-4222-8222-222222222222")
VAULT = TokenVault.from_base64(base64.b64encode(b"a" * 32).decode("ascii"))


class FakeRepository:
    def __init__(self):
        self.connection = ConnectorConnection(
            connection_id=CONNECTION,
            user_id=USER,
            provider="notion",
            account_email="Workspace",
            scopes=(),
            status="connected",
            last_sync_at=None,
            last_error_category=None,
            access_token_expires_at=None,
            access_token_ciphertext=VAULT.encrypt("notion-token"),
            refresh_token_ciphertext=None,
        )
        self.documents = None
        self.synced = False

    async def get_connection(self, access_token, connection_id):
        return self.connection

    async def upsert_documents(self, access_token, documents):
        self.documents = documents

    async def mark_sync(self, access_token, *, connection_id, success, error_category=None):
        self.synced = success


class NotionSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_stores_page_text(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/search":
                return httpx.Response(200, json={
                    "object": "list",
                    "has_more": False,
                    "results": [{
                        "object": "page",
                        "id": "page1",
                        "last_edited_time": "2026-11-11T00:00:00Z",
                        "url": "https://notion.example/page1",
                        "properties": {
                            "Name": {"type": "title", "title": [{"plain_text": "Doc"}]}
                        },
                    }],
                })
            if request.url.path == "/v1/blocks/page1/children":
                return httpx.Response(200, json={
                    "results": [{
                        "id": "p1",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"plain_text": "Hello Notion"}]},
                    }],
                    "has_more": False,
                })
            return httpx.Response(404)

        repository = FakeRepository()
        service = NotionSyncService(
            repository,  # type: ignore[arg-type]
            VAULT,
            transport=httpx.MockTransport(handler),
        )
        result = await service.sync("user-token", CONNECTION, limit=5)
        self.assertEqual(result.listed, 1)
        self.assertEqual(result.ingested, 1)
        self.assertTrue(repository.synced)
        assert repository.documents is not None
        self.assertEqual(repository.documents[0]["provider"], "notion")
        self.assertIn("Hello Notion", repository.documents[0]["content_text"])
