"""Read-only Google Drive connector using the Drive v3 REST API."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from .base import (
    ConnectorDocument,
    ConnectorItem,
    ConnectorPage,
    parse_datetime,
)
from .http import ConnectorHttp

DRIVE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."

DEFAULT_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/html",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "application/pdf",
    "application/vnd.google-apps.drawing": "image/png",
}

FIELDS = (
    "nextPageToken,files("
    "id,name,mimeType,modifiedTime,md5Checksum,version,webViewLink,"
    "parents,size,trashed)"
)


@dataclass(frozen=True)
class GoogleDriveConfig:
    access_token: str
    query: str = "trashed = false"
    drive_id: str | None = None
    include_shared_drives: bool = True
    page_size: int = 100
    max_file_bytes: int = 8 * 1024 * 1024
    export_mime_types: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_EXPORT_MIME_TYPES)
    )


class GoogleDriveConnector:
    provider = "google_drive"

    def __init__(
        self,
        config: GoogleDriveConfig,
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
        page_size = max(1, min(1000, limit, self._config.page_size))
        params = {
            "q": self._config.query,
            "fields": FIELDS,
            "pageSize": str(page_size),
            "supportsAllDrives": "true" if self._config.include_shared_drives else "false",
            "includeItemsFromAllDrives": (
                "true" if self._config.include_shared_drives else "false"
            ),
        }
        if cursor:
            params["pageToken"] = cursor
        if self._config.drive_id:
            params["corpora"] = "drive"
            params["driveId"] = self._config.drive_id
        response = await self._http.request(
            "GET", f"{DRIVE_API}/files", headers=self._headers(), params=params
        )
        payload = response.json()
        items = []
        for file in payload.get("files", []):
            if not isinstance(file, dict) or not file.get("id"):
                continue
            size = file.get("size")
            if str(size or "").isdigit() and int(size) > self._config.max_file_bytes:
                continue
            items.append(self._item(file))
        items = tuple(items)
        return ConnectorPage(items=items, next_cursor=payload.get("nextPageToken"))

    def _item(self, file: dict) -> ConnectorItem:
        mime = file.get("mimeType")
        size = file.get("size")
        return ConnectorItem(
            provider=self.provider,
            external_id=str(file["id"]),
            name=str(file.get("name") or file["id"]),
            kind="document" if str(mime).startswith(GOOGLE_APPS_PREFIX) else "file",
            mime_type=str(mime) if mime else None,
            revision=(
                str(file.get("md5Checksum"))
                if file.get("md5Checksum")
                else str(file.get("version")) if file.get("version") else file.get("modifiedTime")
            ),
            updated_at=parse_datetime(file.get("modifiedTime")),
            url=file.get("webViewLink"),
            parent_id=(file.get("parents") or [None])[0],
            size_bytes=int(size) if size is not None else None,
            metadata={"trashed": bool(file.get("trashed"))},
        )

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument:
        if item.provider != self.provider:
            raise ValueError("item does not belong to this connector")
        file_id = quote(item.external_id, safe="")
        if (item.mime_type or "").startswith(GOOGLE_APPS_PREFIX):
            export_mime = self._config.export_mime_types.get(
                item.mime_type or "", "application/pdf"
            )
            response = await self._http.request(
                "GET",
                f"{DRIVE_API}/files/{file_id}/export",
                headers=self._headers(),
                params={"mimeType": export_mime},
            )
            if len(response.content) > self._config.max_file_bytes:
                raise ValueError("Google Drive document exceeds the configured size limit")
            return ConnectorDocument.build(
                item, response.content, export_mime, revision=item.revision
            )
        response = await self._http.request(
            "GET",
            f"{DRIVE_API}/files/{file_id}",
            headers={**self._headers(), "Accept": "application/octet-stream"},
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        if len(response.content) > self._config.max_file_bytes:
            raise ValueError("Google Drive document exceeds the configured size limit")
        content_type = response.headers.get("content-type", item.mime_type or "application/octet-stream")
        return ConnectorDocument.build(
            item, response.content, content_type.split(";")[0].strip(), revision=item.revision
        )
