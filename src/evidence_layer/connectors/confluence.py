"""Read-only Confluence connector using the REST API."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urljoin

import httpx

from .base import ConnectorDocument, ConnectorItem, ConnectorPage, parse_datetime
from .http import ConnectorHttp


@dataclass(frozen=True)
class ConfluenceConfig:
    base_url: str
    access_token: str
    space_key: str | None = None
    cql: str | None = None
    page_size: int = 50
    max_file_bytes: int = 8 * 1024 * 1024


class ConfluenceConnector:
    provider = "confluence"

    def __init__(
        self,
        config: ConfluenceConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._base = config.base_url.rstrip("/")
        self._http = ConnectorHttp(self.provider, transport=transport)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.access_token}",
            "Accept": "application/json",
        }

    async def list_items(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> ConnectorPage:
        page_size = max(1, min(100, limit, self._config.page_size))
        start = 0
        if cursor:
            try:
                start = max(0, int(cursor))
            except ValueError as exc:
                raise ValueError("invalid Confluence cursor") from exc
        params = {
            "type": "page",
            "limit": str(page_size),
            "start": str(start),
            "expand": "version,space",
        }
        if self._config.cql:
            params["cql"] = self._config.cql
        elif self._config.space_key:
            params["spaceKey"] = self._config.space_key
        response = await self._http.request(
            "GET",
            f"{self._base}/rest/api/content",
            headers=self._headers(),
            params=params,
        )
        payload = response.json()
        base_link = str(payload.get("_links", {}).get("base") or self._base)
        results = payload.get("results", [])
        items = []
        for page in results:
            if not isinstance(page, dict) or not page.get("id"):
                continue
            version = page.get("version") or {}
            links = page.get("_links") or {}
            webui = links.get("webui")
            items.append(
                ConnectorItem(
                    provider=self.provider,
                    external_id=str(page["id"]),
                    name=str(page.get("title") or page["id"]),
                    kind="document",
                    mime_type="text/html",
                    revision=str(version.get("number")) if version.get("number") is not None else None,
                    updated_at=parse_datetime(version.get("when")),
                    url=urljoin(base_link + "/", str(webui).lstrip("/")) if webui else None,
                    parent_id=str((page.get("space") or {}).get("key") or "") or None,
                    metadata={"space": (page.get("space") or {}).get("key")},
                )
            )
        links = payload.get("_links") or {}
        next_cursor = str(start + len(results)) if links.get("next") else None
        return ConnectorPage(items=tuple(items), next_cursor=next_cursor)

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument:
        if item.provider != self.provider:
            raise ValueError("item does not belong to this connector")
        page_id = quote(item.external_id, safe="")
        response = await self._http.request(
            "GET",
            f"{self._base}/rest/api/content/{page_id}",
            headers=self._headers(),
            params={"expand": "body.storage,version,space"},
        )
        payload = response.json()
        body = str((payload.get("body") or {}).get("storage", {}).get("value") or "")
        base_link = str((payload.get("_links") or {}).get("base") or self._base)
        webui = (payload.get("_links") or {}).get("webui")
        enriched = ConnectorItem(
            provider=item.provider,
            external_id=item.external_id,
            name=str(payload.get("title") or item.name),
            kind=item.kind,
            mime_type="text/html",
            revision=str((payload.get("version") or {}).get("number") or item.revision),
            updated_at=item.updated_at,
            url=urljoin(base_link + "/", str(webui).lstrip("/")) if webui else item.url,
            parent_id=item.parent_id,
            size_bytes=len(body.encode("utf-8")),
            metadata=item.metadata,
        )
        payload_bytes = body.encode("utf-8")
        if len(payload_bytes) > self._config.max_file_bytes:
            raise ValueError("Confluence document exceeds the configured size limit")
        return ConnectorDocument.build(
            enriched, payload_bytes, "text/html; charset=utf-8", revision=enriched.revision
        )
