"""Read-only Dropbox connector using the Dropbox HTTP API v2."""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass

import httpx

from .base import ConnectorDocument, ConnectorItem, ConnectorPage, parse_datetime
from .http import ConnectorHttp

DROPBOX_API = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT = "https://content.dropboxapi.com/2"


@dataclass(frozen=True)
class DropboxConfig:
    access_token: str
    path: str = ""
    recursive: bool = True
    max_file_bytes: int = 2 * 1024 * 1024


class DropboxConnector:
    provider = "dropbox"

    def __init__(
        self,
        config: DropboxConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._http = ConnectorHttp(self.provider, transport=transport)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.access_token}",
            "Accept": "application/json",
        }

    async def list_items(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> ConnectorPage:
        page_size = max(1, min(2000, limit))
        if cursor:
            response = await self._http.request(
                "POST",
                f"{DROPBOX_API}/files/list_folder/continue",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"cursor": cursor},
            )
        else:
            response = await self._http.request(
                "POST",
                f"{DROPBOX_API}/files/list_folder",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={
                    "path": self._config.path,
                    "recursive": self._config.recursive,
                    "include_deleted": False,
                    "limit": page_size,
                },
            )
        payload = response.json()
        items: list[ConnectorItem] = []
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict) or entry.get(".tag") != "file":
                continue
            size = entry.get("size")
            if isinstance(size, int) and size > self._config.max_file_bytes:
                continue
            path = str(entry.get("path_display") or entry.get("path_lower") or "")
            mime, _ = mimetypes.guess_type(str(entry.get("name") or path))
            items.append(
                ConnectorItem(
                    provider=self.provider,
                    external_id=str(entry.get("id") or path),
                    name=str(entry.get("name") or path.rsplit("/", 1)[-1]),
                    kind="file",
                    mime_type=mime,
                    revision=str(entry.get("rev") or entry.get("content_hash") or "") or None,
                    updated_at=parse_datetime(entry.get("server_modified")),
                    url=f"https://www.dropbox.com/home{path}" if path else None,
                    parent_id=str(entry.get("parent_shared_folder_id") or "") or None,
                    size_bytes=size if isinstance(size, int) else None,
                    metadata={
                        "path": str(entry.get("path_lower") or path),
                        "content_hash": entry.get("content_hash"),
                    },
                )
            )
            if len(items) >= page_size:
                break
        return ConnectorPage(
            items=tuple(items),
            next_cursor=payload.get("cursor") if payload.get("has_more") else None,
        )

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument:
        if item.provider != self.provider:
            raise ValueError("item does not belong to this connector")
        path = str(item.metadata.get("path") or "")
        response = await self._http.request(
            "POST",
            f"{DROPBOX_CONTENT}/files/download",
            headers={
                "Authorization": f"Bearer {self._config.access_token}",
                "Dropbox-API-Arg": json.dumps({"path": path}),
                "Accept": "application/octet-stream",
            },
        )
        if len(response.content) > self._config.max_file_bytes:
            raise ValueError("Dropbox document exceeds the configured size limit")
        content_type = (
            response.headers.get("content-type", "").split(";")[0].strip()
            or item.mime_type
            or "application/octet-stream"
        )
        return ConnectorDocument.build(
            item, response.content, content_type, revision=item.revision
        )
