"""Read-only GitHub repository connector using the REST API."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .base import ConnectorDocument, ConnectorItem, ConnectorPage
from .http import ConnectorHttp


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    ref: str = "main"
    access_token: str | None = None
    path_prefix: str = ""
    max_file_bytes: int = 2 * 1024 * 1024
    api_base: str = "https://api.github.com"


class GitHubConnector:
    provider = "github"

    def __init__(
        self,
        config: GitHubConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._http = ConnectorHttp(self.provider, transport=transport)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._config.access_token:
            headers["Authorization"] = f"Bearer {self._config.access_token}"
        return headers

    async def list_items(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> ConnectorPage:
        if cursor:
            raise ValueError("GitHub tree listing does not paginate through this connector")
        ref = quote(self._config.ref, safe="")
        response = await self._http.request(
            "GET",
            f"{self._config.api_base}/repos/{self._config.owner}/{self._config.repo}/git/trees/{ref}",
            headers=self._headers(),
            params={"recursive": "1"},
        )
        payload = response.json()
        prefix = self._config.path_prefix.strip("/")
        items: list[ConnectorItem] = []
        for entry in payload.get("tree", []):
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = str(entry.get("path") or "")
            if not path or (prefix and not path.startswith(prefix + "/")):
                continue
            size = entry.get("size")
            if isinstance(size, int) and size > self._config.max_file_bytes:
                continue
            mime, _ = mimetypes.guess_type(path)
            items.append(
                ConnectorItem(
                    provider=self.provider,
                    external_id=f"{self._config.owner}/{self._config.repo}/{path}",
                    name=path.rsplit("/", 1)[-1],
                    kind="file",
                    mime_type=mime,
                    revision=str(entry.get("sha") or "") or None,
                    url=(
                        f"https://github.com/{self._config.owner}/{self._config.repo}"
                        f"/blob/{quote(self._config.ref, safe='/')}/{quote(path, safe='/')}"
                    ),
                    size_bytes=size if isinstance(size, int) else None,
                    metadata={"path": path, "ref": self._config.ref},
                )
            )
            if len(items) >= max(1, limit):
                break
        return ConnectorPage(items=tuple(items), next_cursor=None)

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument:
        if item.provider != self.provider:
            raise ValueError("item does not belong to this connector")
        path = str(item.metadata.get("path") or item.external_id).lstrip("/")
        response = await self._http.request(
            "GET",
            f"{self._config.api_base}/repos/{self._config.owner}/{self._config.repo}/contents/{quote(path, safe='/')}",
            headers=self._headers("application/vnd.github.raw"),
            params={"ref": self._config.ref},
        )
        if len(response.content) > self._config.max_file_bytes:
            raise ValueError("GitHub document exceeds the configured size limit")
        header_type = response.headers.get("content-type", "").split(";")[0].strip()
        content_type = (
            item.mime_type
            if item.mime_type and header_type in ("", "application/octet-stream")
            else header_type or item.mime_type or "application/octet-stream"
        )
        return ConnectorDocument.build(
            item, response.content, content_type, revision=item.revision
        )
