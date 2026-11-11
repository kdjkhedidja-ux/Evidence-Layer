"""Manual Google Drive sync into normalized connector documents.

This is a bounded first ingestion slice: one page per sync, token refresh when
possible, text/binary content stored in forced-RLS tables. It is not the future
connector scheduler or source-snapshot verifier.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from evidence_layer.connectors import ConnectorError, GoogleDriveConfig, GoogleDriveConnector

from evidence_service.connector_crypto import TokenVault, TokenVaultUnavailable
from evidence_service.connector_repository import (
    ConnectorConnection,
    ConnectorDenied,
    ConnectorNotFound,
    ConnectorRepository,
    ConnectorUnavailable,
)

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_SYNC_ITEMS = 25
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class ConnectorSyncError(Exception):
    pass


class ConnectorAuthUnavailable(ConnectorSyncError):
    pass


@dataclass(frozen=True)
class SyncResult:
    connection_id: str
    listed: int
    ingested: int
    skipped: int
    next_cursor: str | None


class GoogleDriveSyncService:
    def __init__(
        self,
        repository: ConnectorRepository,
        vault: TokenVault,
        *,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._vault = vault
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport

    async def sync(
        self, access_token: str, connection_id: Any, *, limit: int = 10
    ) -> SyncResult:
        connection = await self._repository.get_connection(access_token, connection_id)
        if connection.provider != "google_drive":
            raise ConnectorSyncError("unsupported connector provider")
        try:
            provider_token = self._decrypt(connection.access_token_ciphertext)
            if self._needs_refresh(connection):
                provider_token = await self._refresh(
                    access_token, connection, provider_token
                )
            connector = GoogleDriveConnector(
                GoogleDriveConfig(
                    access_token=provider_token,
                    page_size=max(1, min(MAX_SYNC_ITEMS, limit)),
                    max_file_bytes=MAX_DOCUMENT_BYTES,
                ),
                transport=self._transport,
            )
            page = await connector.list_items(limit=max(1, min(MAX_SYNC_ITEMS, limit)))
            documents: list[dict[str, Any]] = []
            skipped = 0
            for item in page.items:
                try:
                    document = await connector.fetch_document(item)
                except ConnectorError:
                    skipped += 1
                    continue
                if len(document.content) > MAX_DOCUMENT_BYTES:
                    skipped += 1
                    continue
                content_type = document.content_type.split(";")[0].strip().lower()
                if self._is_text(content_type):
                    text = document.content.decode("utf-8", errors="replace")
                    content_text = text[: MAX_DOCUMENT_BYTES]
                    content_base64 = None
                else:
                    content_text = None
                    content_base64 = base64.b64encode(document.content).decode("ascii")
                revision = document.revision or item.revision or document.sha256
                documents.append(
                    {
                        "user_id": str(connection.user_id),
                        "connection_id": str(connection.connection_id),
                        "provider": item.provider,
                        "external_id": item.external_id,
                        "name": item.name,
                        "mime_type": item.mime_type or content_type,
                        "revision": revision,
                        "source_url": item.url,
                        "content_text": content_text,
                        "content_base64": content_base64,
                        "content_sha256": document.sha256,
                        "size_bytes": len(document.content),
                        "provider_updated_at": (
                            item.updated_at.isoformat() if item.updated_at else None
                        ),
                    }
                )
            await self._repository.upsert_documents(access_token, documents)
            await self._repository.mark_sync(
                access_token, connection_id=connection.connection_id, success=True
            )
            return SyncResult(
                connection_id=str(connection.connection_id),
                listed=len(page.items),
                ingested=len(documents),
                skipped=skipped,
                next_cursor=page.next_cursor,
            )
        except TokenVaultUnavailable as exc:
            await self._mark_failure(access_token, connection.connection_id, "token_vault")
            raise ConnectorSyncError("token vault is unavailable") from exc
        except ConnectorAuthUnavailable:
            await self._mark_failure(access_token, connection.connection_id, "auth")
            raise
        except (ConnectorDenied, ConnectorNotFound):
            await self._mark_failure(access_token, connection.connection_id, "authorization")
            raise
        except (ConnectorUnavailable, ConnectorError, httpx.HTTPError) as exc:
            await self._mark_failure(access_token, connection.connection_id, "provider")
            raise ConnectorSyncError("connector sync failed") from exc

    def _decrypt(self, ciphertext: str | None) -> str:
        value = self._vault.decrypt(ciphertext)
        if not value:
            raise ConnectorAuthUnavailable
        return value

    def _needs_refresh(self, connection: ConnectorConnection) -> bool:
        expires = connection.access_token_expires_at
        if expires is None:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc) + timedelta(seconds=60)

    async def _refresh(
        self,
        access_token: str,
        connection: ConnectorConnection,
        current_token: str,
    ) -> str:
        refresh_token = self._vault.decrypt(connection.refresh_token_ciphertext)
        if not refresh_token or not self._client_id or not self._client_secret:
            raise ConnectorAuthUnavailable
        try:
            async with httpx.AsyncClient(
                timeout=20.0, transport=self._transport
            ) as client:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
        except httpx.HTTPError as exc:
            raise ConnectorAuthUnavailable from exc
        if response.status_code >= 300:
            raise ConnectorAuthUnavailable
        payload = response.json()
        new_token = payload.get("access_token")
        if not isinstance(new_token, str) or not new_token:
            raise ConnectorAuthUnavailable
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, (int, float))
            else None
        )
        await self._repository.update_tokens(
            access_token,
            connection_id=connection.connection_id,
            access_token_ciphertext=self._vault.encrypt(new_token) or "",
            refresh_token_ciphertext=(
                self._vault.encrypt(str(payload["refresh_token"]))
                if payload.get("refresh_token")
                else connection.refresh_token_ciphertext
            ),
            access_token_expires_at=expires_at,
        )
        return new_token

    def _is_text(self, content_type: str) -> bool:
        return (
            content_type.startswith("text/")
            or content_type
            in {
                "application/json",
                "application/xml",
                "application/xhtml+xml",
                "application/javascript",
                "application/x-ndjson",
                "application/csv",
            }
        )

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
