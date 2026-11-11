"""Manual Notion page sync into normalized connector documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from evidence_layer.connectors import ConnectorError, NotionConfig, NotionConnector

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


class NotionSyncError(Exception):
    pass


@dataclass(frozen=True)
class NotionSyncResult:
    connection_id: str
    listed: int
    ingested: int
    skipped: int
    next_cursor: str | None


class NotionSyncService:
    def __init__(
        self,
        repository: ConnectorRepository,
        vault: TokenVault,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._vault = vault
        self._transport = transport

    async def sync(
        self, access_token: str, connection_id: Any, *, limit: int = 10
    ) -> NotionSyncResult:
        connection = await self._repository.get_connection(access_token, connection_id)
        if connection.provider != "notion":
            raise NotionSyncError("unsupported connector provider")
        try:
            provider_token = self._vault.decrypt(connection.access_token_ciphertext)
            if not provider_token:
                raise NotionSyncError("provider token is unavailable")
            connector = NotionConnector(
                NotionConfig(
                    access_token=provider_token,
                    page_size=max(1, min(MAX_SYNC_ITEMS, limit)),
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
                revision = document.revision or item.revision or document.sha256
                documents.append(
                    {
                        "user_id": str(connection.user_id),
                        "connection_id": str(connection.connection_id),
                        "provider": "notion",
                        "external_id": item.external_id,
                        "name": item.name,
                        "mime_type": "text/plain",
                        "revision": revision,
                        "source_url": item.url,
                        "content_text": document.content.decode("utf-8", errors="replace"),
                        "content_base64": None,
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
            return NotionSyncResult(
                connection_id=str(connection.connection_id),
                listed=len(page.items),
                ingested=len(documents),
                skipped=skipped,
                next_cursor=page.next_cursor,
            )
        except TokenVaultUnavailable as exc:
            await self._mark_failure(access_token, connection.connection_id, "token_vault")
            raise NotionSyncError("token vault is unavailable") from exc
        except (ConnectorDenied, ConnectorNotFound):
            await self._mark_failure(access_token, connection.connection_id, "authorization")
            raise
        except (ConnectorUnavailable, ConnectorError, httpx.HTTPError) as exc:
            await self._mark_failure(access_token, connection.connection_id, "provider")
            raise NotionSyncError("connector sync failed") from exc

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
