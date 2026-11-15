from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx

from evidence_service.connector_repository import ConnectorConnection
from evidence_service.github_app_sync_service import GitHubAppSyncService

USER = UUID("11111111-1111-4111-8111-111111111111")
CONNECTION = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE = UUID("33333333-3333-4333-8333-333333333333")


class FakeRepository:
    def __init__(self):
        self.connection = ConnectorConnection(
            connection_id=CONNECTION,
            user_id=USER,
            provider="github",
            account_email="acme",
            scopes=("contents:read", "metadata:read"),
            status="connected",
            last_sync_at=None,
            last_error_category=None,
            access_token_expires_at=None,
            provider_config={"installation_id": "123"},
            workspace_id=WORKSPACE,
            access_token_ciphertext=None,
            refresh_token_ciphertext=None,
        )
        self.documents = None
        self.evidence_documents = None
        self.synced = False
        self.deleted_external_ids = None

    async def get_connection(self, access_token, connection_id):
        return self.connection

    async def upsert_documents(self, access_token, documents):
        self.documents = documents

    async def mark_sync(self, access_token, *, connection_id, success, error_category=None):
        self.synced = success

    async def personal_workspace_id(self, access_token, user_id):
        return WORKSPACE

    async def set_connection_workspace(self, access_token, connection_id, workspace_id):
        return None

    async def upsert_evidence_documents(self, access_token, *, workspace_id, connection_id, documents):
        self.evidence_documents = documents

    async def delete_stale_connector_evidence(self, access_token, connection_id, external_ids):
        self.deleted_external_ids = external_ids

    async def document_revisions(self, access_token, connection_id):
        return {
            document["external_id"]: document["revision"]
            for document in (self.documents or [])
        }


class GitHubAppSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_installation_repository(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/installation/repositories":
                return httpx.Response(200, json={"repositories": [{
                    "name": "docs",
                    "default_branch": "main",
                    "owner": {"login": "acme"},
                }]})
            if request.url.path == "/repos/acme/docs/git/trees/main":
                return httpx.Response(200, json={"tree": [{
                    "path": "README.md",
                    "type": "blob",
                    "sha": "sha-readme",
                    "size": 5,
                }]})
            if request.url.path == "/repos/acme/docs/contents/README.md":
                return httpx.Response(200, content=b"hello", headers={"content-type": "text/markdown"})
            return httpx.Response(404)

        repository = FakeRepository()
        service = GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            transport=httpx.MockTransport(handler),
        )
        with patch.object(service, "_installation_token", AsyncMock(return_value="install-token")):
            result = await service.sync("user-token", CONNECTION, limit=5)
        self.assertEqual(result.ingested, 1)
        self.assertTrue(repository.synced)
        assert repository.documents is not None
        self.assertEqual(repository.documents[0]["provider"], "github")
        self.assertEqual(repository.documents[0]["content_text"], "hello")
        assert repository.evidence_documents is not None
        self.assertEqual(repository.evidence_documents[0]["content"], "hello")
        self.assertEqual(repository.evidence_documents[0]["connector_external_id"], "acme/docs/README.md")

    async def test_incremental_sync_skips_unchanged_documents(self):
        calls = {"content": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/installation/repositories":
                return httpx.Response(200, json={"repositories": [{
                    "name": "docs",
                    "default_branch": "main",
                    "owner": {"login": "acme"},
                }]})
            if request.url.path == "/repos/acme/docs/git/trees/main":
                return httpx.Response(200, json={"tree": [{
                    "path": "README.md",
                    "type": "blob",
                    "sha": "sha-readme",
                    "size": 5,
                }]})
            if request.url.path == "/repos/acme/docs/contents/README.md":
                calls["content"] += 1
                return httpx.Response(200, content=b"hello", headers={"content-type": "text/markdown"})
            return httpx.Response(404)

        repository = FakeRepository()
        service = GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            transport=httpx.MockTransport(handler),
        )
        with patch.object(service, "_installation_token", AsyncMock(return_value="install-token")):
            first = await service.sync("user-token", CONNECTION, limit=5)
            second = await service.sync("user-token", CONNECTION, limit=5)
        self.assertEqual(first.ingested, 1)
        self.assertEqual(second.ingested, 0)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(calls["content"], 1)
        self.assertEqual(
            repository.deleted_external_ids, {"acme/docs/README.md"}
        )

    async def test_resync_updates_changed_document_content(self):
        state = {"content": b"old body", "sha": "sha-old"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/installation/repositories":
                return httpx.Response(200, json={"repositories": [{
                    "name": "docs",
                    "default_branch": "main",
                    "owner": {"login": "acme"},
                }]})
            if request.url.path == "/repos/acme/docs/git/trees/main":
                return httpx.Response(200, json={"tree": [{
                    "path": "README.md",
                    "type": "blob",
                    "sha": state["sha"],
                    "size": len(state["content"]),
                }]})
            if request.url.path == "/repos/acme/docs/contents/README.md":
                return httpx.Response(
                    200,
                    content=state["content"],
                    headers={"content-type": "text/markdown"},
                )
            return httpx.Response(404)

        repository = FakeRepository()
        service = GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            transport=httpx.MockTransport(handler),
        )
        with patch.object(service, "_installation_token", AsyncMock(return_value="install-token")):
            await service.sync("user-token", CONNECTION, limit=5)
        assert repository.evidence_documents is not None
        self.assertEqual(repository.evidence_documents[0]["content"], "old body")
        self.assertEqual(repository.evidence_documents[0]["connector_revision"], "sha-old")

        state["content"] = b"new body"
        state["sha"] = "sha-new"
        with patch.object(service, "_installation_token", AsyncMock(return_value="install-token")):
            await service.sync("user-token", CONNECTION, limit=5)
        assert repository.evidence_documents is not None
        self.assertEqual(repository.evidence_documents[0]["content"], "new body")
        self.assertEqual(repository.evidence_documents[0]["connector_revision"], "sha-new")
        assert repository.documents is not None
        self.assertEqual(repository.documents[0]["revision"], "sha-new")

    async def test_sync_skips_empty_repository(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/installation/repositories":
                return httpx.Response(200, json={"repositories": [{
                    "name": "empty",
                    "default_branch": "main",
                    "size": 0,
                    "owner": {"login": "acme"},
                }]})
            return httpx.Response(404)

        repository = FakeRepository()
        service = GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            transport=httpx.MockTransport(handler),
        )
        with patch.object(service, "_installation_token", AsyncMock(return_value="install-token")):
            result = await service.sync("user-token", CONNECTION, limit=5)
        self.assertEqual(result.ingested, 0)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(repository.synced)

    async def test_connect_stores_installation_config(self):
        class ConnectRepository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.upserted = None

            async def upsert_connection(self, *args, **kwargs):
                self.upserted = kwargs
                return self.connection

        repository = ConnectRepository()
        service = GitHubAppSyncService(repository, app_id="123", private_key="unused")
        with patch.object(service, "_installation_account", AsyncMock(return_value="acme")):
            await service.connect("user-token", user_id=USER, installation_id=123)
        assert repository.upserted is not None
        self.assertEqual(repository.upserted["provider"], "github")
        self.assertEqual(repository.upserted["provider_config"], {"installation_id": "123"})
        self.assertEqual(repository.upserted["workspace_id"], WORKSPACE)
