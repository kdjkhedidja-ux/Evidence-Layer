"""Read-only Notion connector using the Notion API."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .base import ConnectorDocument, ConnectorItem, ConnectorPage, parse_datetime
from .http import ConnectorHttp


@dataclass(frozen=True)
class NotionConfig:
    access_token: str
    database_id: str | None = None
    page_id: str | None = None
    page_size: int = 50
    max_blocks: int = 2000
    api_base: str = "https://api.notion.com/v1"
    notion_version: str = "2026-03-11"


class NotionConnector:
    provider = "notion"

    def __init__(
        self,
        config: NotionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._http = ConnectorHttp(self.provider, transport=transport)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.access_token}",
            "Notion-Version": self._config.notion_version,
            "Accept": "application/json",
        }

    async def list_items(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> ConnectorPage:
        page_size = max(1, min(100, limit, self._config.page_size))
        if self._config.page_id and not cursor:
            response = await self._http.request(
                "GET",
                f"{self._config.api_base}/pages/{quote(self._config.page_id, safe='')}",
                headers=self._headers(),
            )
            return ConnectorPage(items=(self._item(response.json()),), next_cursor=None)

        if self._config.database_id:
            body = {"page_size": page_size}
            if cursor:
                body["start_cursor"] = cursor
            response = await self._http.request(
                "POST",
                f"{self._config.api_base}/databases/{quote(self._config.database_id, safe='')}/query",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
            )
        else:
            body = {
                "filter": {"value": "page", "property": "object"},
                "page_size": page_size,
            }
            if cursor:
                body["start_cursor"] = cursor
            response = await self._http.request(
                "POST",
                f"{self._config.api_base}/search",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
            )
        payload = response.json()
        items = tuple(
            self._item(page)
            for page in payload.get("results", [])
            if isinstance(page, dict) and page.get("object") == "page" and page.get("id")
        )
        return ConnectorPage(
            items=items,
            next_cursor=payload.get("next_cursor") if payload.get("has_more") else None,
        )

    def _item(self, page: dict) -> ConnectorItem:
        return ConnectorItem(
            provider=self.provider,
            external_id=str(page["id"]),
            name=self._title(page),
            kind="page",
            mime_type="text/plain",
            revision=str(page.get("last_edited_time") or "") or None,
            updated_at=parse_datetime(page.get("last_edited_time")),
            url=page.get("url"),
            parent_id=str((page.get("parent") or {}).get("page_id") or (page.get("parent") or {}).get("database_id") or "") or None,
            metadata={"object": "page"},
        )

    def _title(self, page: dict) -> str:
        properties = page.get("properties") or {}
        if isinstance(properties, dict):
            for prop in properties.values():
                if isinstance(prop, dict) and prop.get("type") == "title":
                    text = "".join(
                        str(part.get("plain_text") or "")
                        for part in prop.get("title", [])
                        if isinstance(part, dict)
                    )
                    if text.strip():
                        return text.strip()
        title = page.get("title")
        if isinstance(title, list):
            text = "".join(
                str(part.get("plain_text") or "")
                for part in title
                if isinstance(part, dict)
            )
            if text.strip():
                return text.strip()
        return str(page.get("id") or "Untitled")

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument:
        if item.provider != self.provider:
            raise ValueError("item does not belong to this connector")
        lines: list[str] = []
        await self._append_children(item.external_id, lines, depth=0, counter=[0])
        text = "\n".join(line for line in lines if line).strip()
        return ConnectorDocument.build(
            item,
            text.encode("utf-8"),
            "text/plain; charset=utf-8",
            revision=item.revision,
        )

    async def _append_children(
        self, block_id: str, lines: list[str], *, depth: int, counter: list[int]
    ) -> None:
        if depth > 2 or counter[0] >= self._config.max_blocks:
            return
        cursor: str | None = None
        while counter[0] < self._config.max_blocks:
            params = {"page_size": "100"}
            if cursor:
                params["start_cursor"] = cursor
            response = await self._http.request(
                "GET",
                f"{self._config.api_base}/blocks/{quote(block_id, safe='')}/children",
                headers=self._headers(),
                params=params,
            )
            payload = response.json()
            for block in payload.get("results", []):
                if not isinstance(block, dict):
                    continue
                counter[0] += 1
                rendered = self._render_block(block)
                if rendered:
                    lines.append(rendered)
                if block.get("has_children") and block.get("id"):
                    await self._append_children(
                        str(block["id"]), lines, depth=depth + 1, counter=counter
                    )
                if counter[0] >= self._config.max_blocks:
                    break
            if not payload.get("has_more") or not payload.get("next_cursor"):
                break
            cursor = str(payload["next_cursor"])

    def _render_block(self, block: dict) -> str:
        block_type = str(block.get("type") or "")
        value = block.get(block_type) if isinstance(block.get(block_type), dict) else {}
        if block_type == "child_page":
            return str(value.get("title") or "")
        if block_type == "table_row":
            cells = value.get("cells") or []
            return " | ".join(
                "".join(
                    str(part.get("plain_text") or "")
                    for part in cell
                    if isinstance(part, dict)
                )
                for cell in cells
            )
        rich_text = value.get("rich_text") or value.get("caption") or []
        if isinstance(rich_text, list):
            return "".join(
                str(part.get("plain_text") or "")
                for part in rich_text
                if isinstance(part, dict)
            )
        return ""
