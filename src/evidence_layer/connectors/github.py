"""Read-only GitHub repository connector using the REST API."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .base import ConnectorDocument, ConnectorItem, ConnectorPage
from .http import ConnectorHttp

PRIORITY_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
SECONDARY_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".c", ".h", ".cpp", ".cs", ".swift", ".sh",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".sql", ".mjs", ".cjs", ".jsonl",
}
# Explicit text types for every allow-listed source extension. The stdlib
# ``mimetypes`` table depends on the host: in the production image .ts, .tsx,
# .toml, .yml and .jsonl had no type (and .ts can even resolve to a Qt
# translation format), so those files were stored as binary and never became
# evidence. A type listed here is always text.
TEXT_MIME_TYPES = {
    ".md": "text/markdown", ".mdx": "text/markdown", ".rst": "text/x-rst",
    ".txt": "text/plain", ".ts": "text/x-typescript", ".tsx": "text/x-typescript",
    ".js": "text/javascript", ".jsx": "text/javascript", ".mjs": "text/javascript",
    ".cjs": "text/javascript", ".py": "text/x-python", ".go": "text/x-go",
    ".rs": "text/x-rust", ".java": "text/x-java", ".kt": "text/x-kotlin",
    ".rb": "text/x-ruby", ".php": "text/x-php", ".c": "text/x-c", ".h": "text/x-c",
    ".cpp": "text/x-c++", ".cs": "text/x-csharp", ".swift": "text/x-swift",
    ".sh": "text/x-shellscript", ".json": "application/json", ".jsonl": "text/plain",
    ".yml": "text/yaml", ".yaml": "text/yaml", ".toml": "text/x-toml",
    ".ini": "text/plain", ".cfg": "text/plain", ".sql": "text/x-sql",
}


def _mime_type(path: str) -> str | None:
    name = path.lower().rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot > 0 and name[dot:] in TEXT_MIME_TYPES:
        return TEXT_MIME_TYPES[name[dot:]]
    return mimetypes.guess_type(path)[0]
SKIP_PARTS = {
    "node_modules", "vendor", "dist", "build", ".next", "coverage",
    ".git", "__pycache__",
}
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum",
}


def _path_priority(path: str) -> int | None:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    parts = set(lower.split("/"))
    if parts & SKIP_PARTS or name in SKIP_NAMES:
        return None
    if name.endswith((".min.js", ".min.css", ".map")):
        return None
    if lower.startswith("readme") or "/readme" in lower:
        return 0
    if lower.startswith("docs/") or "/docs/" in lower:
        return 1
    if any(name.endswith(ext) for ext in PRIORITY_EXTENSIONS):
        return 2
    if any(name.endswith(ext) for ext in SECONDARY_EXTENSIONS):
        return 3
    return None



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
        candidate_pairs = []
        for entry in payload.get("tree", []):
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = str(entry.get("path") or "")
            if not path or (prefix and not path.startswith(prefix + "/")):
                continue
            priority = _path_priority(path)
            if priority is None:
                continue
            size = entry.get("size")
            if isinstance(size, int) and size > self._config.max_file_bytes:
                continue
            mime = _mime_type(path)
            item = ConnectorItem(
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
                metadata={"path": path, "ref": self._config.ref, "priority": priority},
            )
            candidate_pairs.append((priority, path, item))
        candidate_pairs.sort(key=lambda pair: (pair[0], pair[1]))
        capped = max(1, limit)
        return ConnectorPage(
            items=tuple(item for _, _, item in candidate_pairs[:capped]),
            next_cursor=None,
            complete=payload.get("truncated") is not True and len(candidate_pairs) <= capped,
        )

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
            if item.mime_type
            and header_type in ("", "application/octet-stream", "application/vnd.github.raw")
            else header_type or item.mime_type or "application/octet-stream"
        )
        return ConnectorDocument.build(
            item, response.content, content_type, revision=item.revision
        )
