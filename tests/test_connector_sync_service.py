from __future__ import annotations

import base64
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

from evidence_service.connector_crypto import TokenVault
from evidence_service.connector_repository import ConnectorConnection
from evidence_service.connector_sync_service import GoogleDriveSyncService

USER = UUID("11111111-1111-4111-8111-111111111111")
CONNECTION = UUID("22222222-2222-4222-8222-222222222222")
VAULT = TokenVault.from_base64(base64.b64encode(b"a" * 32).decode("ascii"))


class FakeRepository:
    def __init__(self):
        self.connection = ConnectorConnection(
            connection_id=CONNECTION,
            user_id=USER,
            provider="google_drive",
            account_email="owner@example.test",
            scopes=("drive.readonly",),
            status="connected",
            last_sync_at=None,
            last_error_category=None,
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            access_token_ciphertext=VAULT.encrypt("drive-token"),
            refresh_token_ciphertext=None,
        )
        self.documents = None
        self.synced = False
        self.deleted = False

    async def get_connection(self, access_token, connection_id):
        return self.connection

    async def upsert_documents(self, access_token, documents):
        self.documents = documents

    async def mark_sync(self, access_token, *, connection_id, success, error_category=None):
        self.synced = success

    async def delete_connection(self, access_token, connection_id):
        self.deleted = True


class ConnectorSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_drive_sync_stores_text_document(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/drive/v3/files":
                return httpx.Response(200, json={"files": [{
                    "id": "doc1",
                    "name": "Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-11-11T00:00:00Z",
                    "version": "2",
                }]})
            if request.url.path == "/drive/v3/files/doc1/export":
                return httpx.Response(200, content=b"<p>hello</p>", headers={"content-type": "text/html"})
            return httpx.Response(404)

        repository = FakeRepository()
        service = GoogleDriveSyncService(
            repository,  # type: ignore[arg-type]
            VAULT,
            client_id="",
            client_secret="",
            transport=httpx.MockTransport(handler),
        )
        result = await service.sync("user-token", CONNECTION, limit=5)
        self.assertEqual(result.listed, 1)
        self.assertEqual(result.ingested, 1)
        self.assertTrue(repository.synced)
        assert repository.documents is not None
        self.assertEqual(repository.documents[0]["content_text"], "<p>hello</p>")
        self.assertEqual(repository.documents[0]["revision"], "2")

    async def test_disconnect_revokes_then_deletes(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host, request.url.path))
            return httpx.Response(200, json={"ok": True})

        repository = FakeRepository()
        service = GoogleDriveSyncService(
            repository,  # type: ignore[arg-type]
            VAULT,
            client_id="",
            client_secret="",
            transport=httpx.MockTransport(handler),
        )
        await service.disconnect("user-token", CONNECTION)
        assert repository.deleted is True
        self.assertTrue(any(host == "oauth2.googleapis.com" for host, _ in seen))
