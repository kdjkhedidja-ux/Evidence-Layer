from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx

from evidence_service.connector_repository import ConnectorConnection, ConnectorUnavailable
from evidence_service.github_app_sync_service import (
    GitHubAccountUnavailable,
    GitHubAppSyncError,
    GitHubAppSyncService,
    GitHubInstallationNotOwned,
)

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
        self.allowed = True

    async def get_connection(self, access_token, connection_id):
        return self.connection

    async def sync_allowed(self, access_token, connection_id):
        return self.allowed

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

    def _connect_service(self, handler, *, client_id="client-id", client_secret="client-secret"):
        class ConnectRepository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.upserted = None

            async def upsert_connection(self, *args, **kwargs):
                self.upserted = kwargs
                return self.connection

        repository = ConnectRepository()
        service = GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            client_id=client_id,
            client_secret=client_secret,
            transport=httpx.MockTransport(handler),
        )
        return repository, service

    @staticmethod
    def _oauth_handler(installations, *, code="install-code"):
        seen = {"token_exchange": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login/oauth/access_token":
                seen["token_exchange"] += 1
                body = dict(httpx.QueryParams(request.content.decode()))
                if body.get("code") != code or body.get("client_secret") != "client-secret":
                    return httpx.Response(200, json={"error": "bad_verification_code"})
                return httpx.Response(200, json={"access_token": "ghu_user", "token_type": "bearer"})
            if request.url.path == "/user/installations":
                if request.headers.get("authorization") != "Bearer ghu_user":
                    return httpx.Response(401)
                return httpx.Response(200, json={
                    "total_count": len(installations),
                    "installations": [{"id": value} for value in installations],
                })
            return httpx.Response(404)

        return handler, seen

    async def test_connect_stores_installation_config_after_ownership_check(self):
        handler, seen = self._oauth_handler([7, 123])
        repository, service = self._connect_service(handler)
        with patch.object(service, "_installation_account", AsyncMock(return_value="acme")):
            await service.connect(
                "user-token", user_id=USER, installation_id=123, oauth_code="install-code"
            )
        self.assertEqual(seen["token_exchange"], 1)
        assert repository.upserted is not None
        self.assertEqual(repository.upserted["provider"], "github")
        self.assertEqual(repository.upserted["provider_config"], {"installation_id": "123"})
        self.assertEqual(repository.upserted["workspace_id"], WORKSPACE)

    async def test_connect_refuses_installation_of_another_account(self):
        # The attacker completes their own install flow, then swaps the
        # installation_id in the callback URL for a victim's installation.
        handler, _ = self._oauth_handler([7])
        repository, service = self._connect_service(handler)
        account = AsyncMock(return_value="victim")
        with patch.object(service, "_installation_account", account):
            with self.assertRaises(GitHubInstallationNotOwned):
                await service.connect(
                    "user-token", user_id=USER, installation_id=123, oauth_code="install-code"
                )
        self.assertIsNone(repository.upserted)
        account.assert_not_awaited()

    async def test_connect_refuses_forged_or_reused_code(self):
        handler, _ = self._oauth_handler([123])
        repository, service = self._connect_service(handler)
        with self.assertRaises(GitHubInstallationNotOwned):
            await service.connect(
                "user-token", user_id=USER, installation_id=123, oauth_code="forged"
            )
        self.assertIsNone(repository.upserted)

    async def test_connect_requires_oauth_code(self):
        handler, seen = self._oauth_handler([123])
        repository, service = self._connect_service(handler)
        with self.assertRaises(GitHubInstallationNotOwned):
            await service.connect(
                "user-token", user_id=USER, installation_id=123, oauth_code=""
            )
        self.assertEqual(seen["token_exchange"], 0)
        self.assertIsNone(repository.upserted)

    async def test_connect_fails_closed_without_client_credentials(self):
        handler, seen = self._oauth_handler([123])
        repository, service = self._connect_service(handler, client_id="", client_secret="")
        with self.assertRaises(GitHubAppSyncError):
            await service.connect(
                "user-token", user_id=USER, installation_id=123, oauth_code="install-code"
            )
        self.assertEqual(seen["token_exchange"], 0)
        self.assertIsNone(repository.upserted)

    async def test_connect_searches_every_installation_page(self):
        installations = list(range(1000, 1100)) + [123]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login/oauth/access_token":
                return httpx.Response(200, json={"access_token": "ghu_user"})
            if request.url.path == "/user/installations":
                page = int(request.url.params.get("page", "1"))
                chunk = installations[(page - 1) * 100 : page * 100]
                return httpx.Response(200, json={"installations": [{"id": v} for v in chunk]})
            return httpx.Response(404)

        repository, service = self._connect_service(handler)
        with patch.object(service, "_installation_account", AsyncMock(return_value="acme")):
            await service.connect(
                "user-token", user_id=USER, installation_id=123, oauth_code="install-code"
            )
        self.assertIsNotNone(repository.upserted)


def _repo_handler(paths, *, truncated=False, total_count=None, content_calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/installation/repositories":
            payload = {"repositories": [{
                "name": "docs",
                "default_branch": "main",
                "owner": {"login": "acme"},
            }]}
            if total_count is not None:
                payload["total_count"] = total_count
            return httpx.Response(200, json=payload)
        if request.url.path == "/repos/acme/docs/git/trees/main":
            return httpx.Response(200, json={
                "truncated": truncated,
                "tree": [
                    {"path": path, "type": "blob", "sha": f"sha-{path}", "size": 4}
                    for path in paths
                ],
            })
        if request.url.path.startswith("/repos/acme/docs/contents/"):
            if content_calls is not None:
                content_calls.append(request.url.path)
            return httpx.Response(200, content=b"body", headers={"content-type": "text/markdown"})
        return httpx.Response(404)

    return handler


class GitHubSyncSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, handler, repository):
        return GitHubAppSyncService(
            repository,  # type: ignore[arg-type]
            app_id="123",
            private_key="unused",
            transport=httpx.MockTransport(handler),
        )

    async def test_large_repository_never_deletes_unfetched_files(self):
        paths = [f"docs/file{index:03}.md" for index in range(250)]
        repository = FakeRepository()
        service = self._service(_repo_handler(paths), repository)
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            first = await service.sync("token", CONNECTION, limit=5)
        self.assertEqual(first.ingested, 100)
        self.assertEqual(first.pending, 150)
        self.assertEqual(first.listed, 250)
        # The whole tree was listed, so the stale set is the full file list and
        # nothing that still exists is removed.
        self.assertEqual(
            repository.deleted_external_ids, {f"acme/docs/{path}" for path in paths}
        )

    async def test_later_runs_fetch_the_remaining_files(self):
        paths = [f"docs/file{index:03}.md" for index in range(250)]
        repository = FakeRepository()
        stored: dict[str, str] = {}

        async def upsert_documents(access_token, documents):
            for document in documents:
                stored[document["external_id"]] = document["revision"]

        async def document_revisions(access_token, connection_id):
            return dict(stored)

        repository.upsert_documents = upsert_documents  # type: ignore[method-assign]
        repository.document_revisions = document_revisions  # type: ignore[method-assign]
        service = self._service(_repo_handler(paths), repository)
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            results = [await service.sync("token", CONNECTION, limit=5) for _ in range(3)]
        self.assertEqual([r.ingested for r in results], [100, 100, 50])
        self.assertEqual(results[-1].pending, 0)
        self.assertEqual(len(stored), 250)

    async def test_truncated_tree_skips_stale_deletion(self):
        repository = FakeRepository()
        service = self._service(_repo_handler(["README.md"], truncated=True), repository)
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            await service.sync("token", CONNECTION, limit=5)
        self.assertIsNone(repository.deleted_external_ids)

    async def test_unread_repositories_skip_stale_deletion(self):
        repository = FakeRepository()
        service = self._service(_repo_handler(["README.md"], total_count=9), repository)
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            await service.sync("token", CONNECTION, limit=5)
        self.assertIsNone(repository.deleted_external_ids)

    async def test_failed_evidence_write_does_not_record_revisions(self):
        repository = FakeRepository()
        repository.upsert_evidence_documents = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectorUnavailable()
        )
        service = self._service(_repo_handler(["README.md"]), repository)
        with patch.object(service, "_installation_token", AsyncMock(return_value="t")):
            with self.assertRaises(GitHubAppSyncError):
                await service.sync("token", CONNECTION, limit=5)
        # Revisions stay unrecorded, so the next run retries the changed file
        # instead of treating it as unchanged forever.
        self.assertIsNone(repository.documents)
        self.assertIsNone(repository.deleted_external_ids)

    async def test_account_being_deleted_is_not_synced(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return _repo_handler(["README.md"])(request)

        repository = FakeRepository()
        repository.allowed = False
        service = self._service(handler, repository)
        token = AsyncMock(return_value="t")
        with patch.object(service, "_installation_token", token):
            with self.assertRaises(GitHubAccountUnavailable):
                await service.sync("token", CONNECTION, limit=5)
        token.assert_not_awaited()
        self.assertEqual(calls, [])
        self.assertIsNone(repository.documents)
        self.assertIsNone(repository.evidence_documents)
        self.assertFalse(repository.synced)
