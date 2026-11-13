"""GitHub App installation connector: read-only repository ingestion."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import jwt

from evidence_layer.connectors import ConnectorError, GitHubConfig, GitHubConnector

from evidence_service.connector_repository import (
    ConnectorConnection,
    ConnectorDenied,
    ConnectorNotFound,
    ConnectorRepository,
    ConnectorUnavailable,
)

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_SYNC_ITEMS = 100
MAX_REPOSITORIES = 5
MAX_TOTAL_BYTES = 12 * 1024 * 1024


class GitHubAppSyncError(Exception):
    pass


@dataclass(frozen=True)
class GitHubSyncResult:
    connection_id: str
    listed: int
    ingested: int
    skipped: int
    next_cursor: str | None


class GitHubAppSyncService:
    provider = "github"

    def __init__(
        self,
        repository: ConnectorRepository,
        *,
        app_id: str,
        private_key: str,
        api_base: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._app_id = app_id
        self._private_key = private_key.replace("\\n", "\n")
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    def _app_jwt(self) -> str:
        now = int(datetime.now(timezone.utc).timestamp())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self._app_id},
            self._private_key,
            algorithm="RS256",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=20.0, transport=self._transport
            ) as client:
                return await client.request(
                    method,
                    f"{self._api_base}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "evidence-layer-connector/0.1",
                    },
                    json=body,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise GitHubAppSyncError("GitHub API request failed") from exc

    async def _installation_account(self, installation_id: int) -> str | None:
        response = await self._request(
            "GET", f"/app/installations/{installation_id}", token=self._app_jwt()
        )
        if response.status_code >= 300:
            raise GitHubAppSyncError("GitHub installation is unavailable")
        payload = response.json()
        account = payload.get("account") if isinstance(payload, dict) else None
        return str(account.get("login")) if isinstance(account, dict) and account.get("login") else None

    async def _installation_token(self, installation_id: int) -> str:
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=self._app_jwt(),
        )
        if response.status_code >= 300:
            raise GitHubAppSyncError("GitHub installation token is unavailable")
        payload = response.json()
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise GitHubAppSyncError("GitHub installation token is invalid")
        return token

    async def connect(
        self, access_token: str, *, user_id: UUID, installation_id: int
    ) -> ConnectorConnection:
        try:
            account_login = await self._installation_account(installation_id)
            workspace_id = await self._repository.personal_workspace_id(
                access_token, user_id
            )
            if workspace_id is None:
                raise GitHubAppSyncError("a personal workspace is required")
            return await self._repository.upsert_connection(
                access_token,
                user_id=user_id,
                provider="github",
                account_email=account_login,
                scopes=["contents:read", "metadata:read"],
                access_token_ciphertext=None,
                refresh_token_ciphertext=None,
                access_token_expires_at=None,
                provider_config={"installation_id": str(installation_id)},
                workspace_id=workspace_id,
            )
        except (ConnectorDenied, ConnectorNotFound):
            raise
        except ConnectorUnavailable:
            raise
        except Exception as exc:
            if isinstance(exc, GitHubAppSyncError):
                raise
            raise GitHubAppSyncError("GitHub connection failed") from exc

    async def sync(
        self, access_token: str, connection_id: Any, *, limit: int = 10
    ) -> GitHubSyncResult:
        connection = await self._repository.get_connection(access_token, connection_id)
        if connection.provider != "github":
            raise GitHubAppSyncError("unsupported connector provider")
        installation_id_raw = connection.provider_config.get("installation_id")
        try:
            installation_id = int(installation_id_raw)
        except (TypeError, ValueError) as exc:
            raise GitHubAppSyncError("GitHub installation is unavailable") from exc
        try:
            installation_token = await self._installation_token(installation_id)
            response = await self._request(
                "GET",
                "/installation/repositories",
                token=installation_token,
                params={"per_page": str(min(MAX_REPOSITORIES, max(1, limit)))},
            )
            if response.status_code >= 300:
                raise GitHubAppSyncError("GitHub repositories are unavailable")
            payload = response.json()
            repositories = payload.get("repositories", []) if isinstance(payload, dict) else []
            documents: list[dict[str, Any]] = []
            listed = 0
            skipped = 0
            total_bytes = 0
            had_errors = False
            for repository in repositories[:MAX_REPOSITORIES]:
                if not isinstance(repository, dict):
                    continue
                owner = (repository.get("owner") or {}).get("login")
                name = repository.get("name")
                branch = repository.get("default_branch") or "main"
                if not owner or not name:
                    continue
                if repository.get("size") == 0:
                    skipped += 1
                    continue
                connector = GitHubConnector(
                    GitHubConfig(
                        owner=str(owner),
                        repo=str(name),
                        ref=str(branch),
                        access_token=installation_token,
                        max_file_bytes=MAX_DOCUMENT_BYTES,
                    ),
                    transport=self._transport,
                )
                remaining = MAX_SYNC_ITEMS - len(documents)
                if remaining <= 0:
                    break
                try:
                    page = await connector.list_items(limit=min(50, remaining))
                except ConnectorError:
                    skipped += 1
                    had_errors = True
                    continue
                listed += len(page.items)
                for item in page.items:
                    try:
                        document = await connector.fetch_document(item)
                    except ConnectorError:
                        skipped += 1
                        had_errors = True
                        continue
                    if (
                        len(document.content) > MAX_DOCUMENT_BYTES
                        or total_bytes + len(document.content) > MAX_TOTAL_BYTES
                    ):
                        skipped += 1
                        continue
                    total_bytes += len(document.content)
                    content_type = document.content_type.split(";")[0].strip().lower()
                    if content_type.startswith("text/") or content_type in {
                        "application/json", "application/xml", "application/xhtml+xml"
                    }:
                        content_text = document.content.decode("utf-8", errors="replace")
                        content_base64 = None
                    else:
                        content_text = None
                        content_base64 = base64.b64encode(document.content).decode("ascii")
                    revision = document.revision or item.revision or document.sha256
                    documents.append({
                        "user_id": str(connection.user_id),
                        "connection_id": str(connection.connection_id),
                        "provider": "github",
                        "external_id": item.external_id,
                        "name": item.name,
                        "mime_type": item.mime_type or content_type,
                        "revision": revision,
                        "source_url": item.url,
                        "content_text": content_text,
                        "content_base64": content_base64,
                        "content_sha256": document.sha256,
                        "size_bytes": len(document.content),
                        "provider_updated_at": None,
                    })
                    if len(documents) >= MAX_SYNC_ITEMS:
                        break
                if len(documents) >= MAX_SYNC_ITEMS:
                    break
            await self._repository.upsert_documents(access_token, documents)
            if connection.workspace_id is None:
                workspace_id = await self._repository.personal_workspace_id(
                    access_token, connection.user_id
                )
                if workspace_id is None:
                    raise GitHubAppSyncError("a personal workspace is required")
                await self._repository.set_connection_workspace(
                    access_token, connection.connection_id, workspace_id
                )
                connection = ConnectorConnection(
                    connection_id=connection.connection_id,
                    user_id=connection.user_id,
                    provider=connection.provider,
                    account_email=connection.account_email,
                    scopes=connection.scopes,
                    status=connection.status,
                    last_sync_at=connection.last_sync_at,
                    last_error_category=connection.last_error_category,
                    access_token_expires_at=connection.access_token_expires_at,
                    provider_config=connection.provider_config,
                    workspace_id=workspace_id,
                )
            evidence_documents = [
                {
                    "content": doc["content_text"],
                    "source_label": f"GitHub: {doc['external_id']}"[:512],
                    "connector_external_id": doc["external_id"],
                    "connector_revision": doc["revision"],
                }
                for doc in documents
                if doc.get("content_text")
            ]
            await self._repository.upsert_evidence_documents(
                access_token,
                workspace_id=connection.workspace_id,
                connection_id=connection.connection_id,
                documents=evidence_documents,
            )
            if not had_errors:
                await self._repository.delete_stale_connector_evidence(
                    access_token,
                    connection.connection_id,
                    {doc["external_id"] for doc in documents},
                )
            await self._repository.mark_sync(
                access_token,
                connection_id=connection.connection_id,
                success=True,
                error_category="partial" if had_errors else None,
            )
            return GitHubSyncResult(
                connection_id=str(connection.connection_id),
                listed=listed,
                ingested=len(documents),
                skipped=skipped,
                next_cursor=None,
            )
        except (ConnectorDenied, ConnectorNotFound):
            await self._mark_failure(access_token, connection.connection_id, "authorization")
            raise
        except (ConnectorUnavailable, ConnectorError, httpx.HTTPError, GitHubAppSyncError) as exc:
            await self._mark_failure(access_token, connection.connection_id, "provider")
            if isinstance(exc, GitHubAppSyncError):
                raise
            raise GitHubAppSyncError("GitHub sync failed") from exc

    async def disconnect(self, access_token: str, connection_id: Any) -> None:
        await self._repository.delete_connection(access_token, connection_id)

    async def _mark_failure(self, access_token: str, connection_id: Any, category: str) -> None:
        try:
            await self._repository.mark_sync(
                access_token,
                connection_id=connection_id,
                success=False,
                error_category=category,
            )
        except Exception:
            pass
