"""GitHub App installation connector: read-only repository ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import jwt

from evidence_layer.connectors import ConnectorError, GitHubConfig, GitHubConnector

from evidence_service.connector_parts import (
    EMPTY_SHA256,
    MAX_PARTS,
    SINGLE_RECORD_MAX_CHARS,
    log_path_hash,
    not_indexed_mime,
    not_indexed_reason,
    part_external_id,
    part_label,
    possible_part_ids,
    split_parts,
)
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


MAX_TREE_ITEMS = 10_000
# Evidence upsert batches keep one RPC body well under the gateway limit now
# that one large file can produce dozens of part records.
EVIDENCE_BATCH_RECORDS = 50
EVIDENCE_BATCH_CHARS = 2_000_000
NOT_INDEXED_LOG_LIMIT = 20
_TEXT_CONTENT_TYPES = {"application/json", "application/xml", "application/xhtml+xml"}
MAX_USER_INSTALLATION_PAGES = 10


class GitHubAppSyncError(Exception):
    pass


class GitHubAccountUnavailable(GitHubAppSyncError):
    """The connection owner's account is being deleted; nothing is synced."""


class GitHubInstallationNotOwned(GitHubAppSyncError):
    """The signed-in GitHub user cannot access the requested installation."""


@dataclass(frozen=True)
class GitHubSyncResult:
    connection_id: str
    listed: int
    ingested: int
    skipped: int
    next_cursor: str | None
    unchanged: int = 0
    pending: int = 0
    not_indexed: int = 0
    # (truncated path hash, reason) for files recorded as not indexed in this
    # run; plain paths are never logged.
    not_indexed_files: tuple[tuple[str, str], ...] = ()


class GitHubAppSyncService:
    provider = "github"

    def __init__(
        self,
        repository: ConnectorRepository,
        *,
        app_id: str,
        private_key: str,
        client_id: str = "",
        client_secret: str = "",
        api_base: str = "https://api.github.com",
        oauth_base: str = "https://github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._app_id = app_id
        self._private_key = private_key.replace("\\n", "\n")
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_base = api_base.rstrip("/")
        self._oauth_base = oauth_base.rstrip("/")
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

    async def _user_token(self, oauth_code: str) -> str:
        if not self._client_id or not self._client_secret:
            raise GitHubAppSyncError("GitHub user authorization is not configured")
        try:
            async with httpx.AsyncClient(
                timeout=20.0, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._oauth_base}/login/oauth/access_token",
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "evidence-layer-connector/0.1",
                    },
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "code": oauth_code,
                    },
                )
        except httpx.HTTPError as exc:
            raise GitHubAppSyncError("GitHub user authorization failed") from exc
        if response.status_code >= 300:
            raise GitHubInstallationNotOwned("GitHub user authorization was refused")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubAppSyncError("GitHub user authorization failed") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            # GitHub answers 200 with {"error": "bad_verification_code"} for
            # expired, reused or forged codes.
            raise GitHubInstallationNotOwned("GitHub user authorization was refused")
        return token

    async def _user_can_access_installation(
        self, user_token: str, installation_id: int
    ) -> bool:
        for page in range(1, MAX_USER_INSTALLATION_PAGES + 1):
            response = await self._request(
                "GET",
                "/user/installations",
                token=user_token,
                params={"per_page": "100", "page": str(page)},
            )
            if response.status_code in (401, 403):
                return False
            if response.status_code >= 300:
                raise GitHubAppSyncError("GitHub installations are unavailable")
            payload = response.json()
            installations = (
                payload.get("installations") if isinstance(payload, dict) else None
            )
            if not isinstance(installations, list):
                raise GitHubAppSyncError("GitHub installations are unavailable")
            for installation in installations:
                if isinstance(installation, dict) and installation.get("id") == installation_id:
                    return True
            if len(installations) < 100:
                return False
        return False

    async def connect(
        self,
        access_token: str,
        *,
        user_id: UUID,
        installation_id: int,
        oauth_code: str,
    ) -> ConnectorConnection:
        """Link an installation only after proving the GitHub user can access it.

        The installation id arrives through a browser redirect and can be
        edited by anyone, so it is never trusted on its own: the one-time
        OAuth code GitHub issues during installation is exchanged for a
        user-to-server token, and the installation must appear in that user's
        ``/user/installations`` list.
        """
        try:
            if not oauth_code:
                raise GitHubInstallationNotOwned("GitHub user authorization is required")
            user_token = await self._user_token(oauth_code)
            if not await self._user_can_access_installation(user_token, installation_id):
                raise GitHubInstallationNotOwned(
                    "GitHub installation is not accessible to this user"
                )
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
        if not await self._repository.sync_allowed(access_token, connection.connection_id):
            raise GitHubAccountUnavailable("connection owner account is not available")
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
            if not isinstance(repositories, list):
                raise GitHubAppSyncError("GitHub repositories are unavailable")
            considered = repositories[:MAX_REPOSITORIES]
            total_count = payload.get("total_count") if isinstance(payload, dict) else None
            # Stale evidence may only be removed when every repository and every
            # file of the installation was listed in this run. Anything less
            # (more repositories than we read, a truncated tree, a listing error)
            # would make unlisted files look deleted.
            listing_complete = not (
                isinstance(total_count, int) and total_count > len(considered)
            )
            try:
                known_states = await self._repository.document_states(
                    access_token, connection.connection_id
                )
            except Exception:
                known_states = {}
            documents: list[dict[str, Any]] = []
            evidence_documents: list[dict[str, Any]] = []
            not_indexed: list[tuple[str, str]] = []
            fetched = 0
            indexed = 0
            current_external_ids: set[str] = set()
            listed = 0
            unchanged = 0
            skipped = 0
            pending = 0
            total_bytes = 0
            had_errors = False
            for repository in considered:
                if not isinstance(repository, dict):
                    listing_complete = False
                    continue
                owner = (repository.get("owner") or {}).get("login")
                name = repository.get("name")
                branch = repository.get("default_branch") or "main"
                if not owner or not name:
                    listing_complete = False
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
                try:
                    page = await connector.list_items(
                        limit=MAX_TREE_ITEMS, include_oversized=True
                    )
                except ConnectorError:
                    skipped += 1
                    had_errors = True
                    continue
                if not page.complete:
                    listing_complete = False
                listed += len(page.items)
                for item in page.items:
                    path = str(item.metadata.get("path") or item.external_id)
                    current_external_ids.add(item.external_id)
                    # Existing parts of a possibly large file are kept unless
                    # this run re-indexes the file (handled below).
                    may_have_parts = (
                        not isinstance(item.size_bytes, int)
                        or item.size_bytes > SINGLE_RECORD_MAX_CHARS
                    )
                    if may_have_parts:
                        current_external_ids.update(possible_part_ids(item.external_id))
                    known = known_states.get(item.external_id)
                    if (
                        item.revision
                        and known is not None
                        and known[0] == item.revision
                        and (
                            not_indexed_reason(known[1]) is not None
                            or item.mime_type is None
                            or known[1] == item.mime_type
                        )
                    ):
                        # Same revision: either already indexed with the right
                        # type, or already recorded as not indexed.
                        unchanged += 1
                        continue
                    if item.metadata.get("not_indexed") == "too_large":
                        documents.append(self._not_indexed_document(
                            connection, item, "too_large"
                        ))
                        not_indexed.append((log_path_hash(connection.connection_id, path), "too_large"))
                        continue
                    if fetched >= MAX_SYNC_ITEMS:
                        # Fetched on a later run; its current evidence is kept.
                        pending += 1
                        continue
                    try:
                        document = await connector.fetch_document(item)
                    except ConnectorError:
                        fetched += 1
                        had_errors = True
                        documents.append(self._not_indexed_document(
                            connection, item, "fetch_error"
                        ))
                        not_indexed.append((log_path_hash(connection.connection_id, path), "fetch_error"))
                        continue
                    if total_bytes + len(document.content) > MAX_TOTAL_BYTES:
                        # Per-run byte budget: not an exclusion, fetched later.
                        pending += 1
                        continue
                    fetched += 1
                    total_bytes += len(document.content)
                    if len(document.content) > MAX_DOCUMENT_BYTES:
                        reason = "too_large"
                    else:
                        content_type = document.content_type.split(";")[0].strip().lower()
                        is_text = (
                            content_type.startswith("text/") or content_type in _TEXT_CONTENT_TYPES
                        ) and b"\x00" not in document.content  # NUL: binary (and invalid in Postgres text)
                        content_text = (
                            document.content.decode("utf-8", errors="replace") if is_text else None
                        )
                        if content_text is None:
                            reason = "binary"
                        elif not content_text.strip():
                            reason = "empty"
                        elif len(split_parts(content_text)) > MAX_PARTS:
                            reason = "too_many_parts"
                        else:
                            reason = None
                    if reason is not None:
                        # The file was read and is not indexable now: drop any
                        # evidence it had (a fetch error above keeps it).
                        current_external_ids.difference_update(possible_part_ids(item.external_id))
                        current_external_ids.discard(item.external_id)
                        documents.append(self._not_indexed_document(connection, item, reason))
                        not_indexed.append((log_path_hash(connection.connection_id, path), reason))
                        continue
                    revision = document.revision or item.revision or document.sha256
                    # This file is re-indexed now: keep exactly its new ids so
                    # obsolete parts (or a former single record) are removed.
                    current_external_ids.difference_update(possible_part_ids(item.external_id))
                    current_external_ids.discard(item.external_id)
                    records = self._evidence_records(item.external_id, content_text, revision)
                    current_external_ids.update(record["connector_external_id"] for record in records)
                    evidence_documents.extend(records)
                    indexed += 1
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
                        "content_base64": None,
                        "content_sha256": document.sha256,
                        "size_bytes": len(document.content),
                        "provider_updated_at": None,
                    })
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
            for batch in self._evidence_batches(evidence_documents):
                await self._repository.upsert_evidence_documents(
                    access_token,
                    workspace_id=connection.workspace_id,
                    connection_id=connection.connection_id,
                    documents=batch,
                )
            # Revisions are recorded only after the evidence write succeeded.
            # They drive the "unchanged" shortcut, so recording them first would
            # leave evidence permanently stale whenever the evidence write fails.
            await self._repository.upsert_documents(access_token, documents)
            if not had_errors and listing_complete:
                await self._repository.delete_stale_connector_evidence(
                    access_token,
                    connection.connection_id,
                    current_external_ids,
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
                ingested=indexed,
                skipped=skipped,
                next_cursor=None,
                unchanged=unchanged,
                pending=pending,
                not_indexed=len(not_indexed),
                not_indexed_files=tuple(not_indexed[:NOT_INDEXED_LOG_LIMIT]),
            )
        except (ConnectorDenied, ConnectorNotFound):
            await self._mark_failure(access_token, connection.connection_id, "authorization")
            raise
        except (ConnectorUnavailable, ConnectorError, httpx.HTTPError, GitHubAppSyncError) as exc:
            await self._mark_failure(access_token, connection.connection_id, "provider")
            if isinstance(exc, GitHubAppSyncError):
                raise
            raise GitHubAppSyncError("GitHub sync failed") from exc

    @staticmethod
    def _evidence_records(external_id: str, text: str, revision: str) -> list[dict[str, Any]]:
        """One record for a normal file; labelled, offset-carrying parts for a large one."""
        base_label = f"GitHub: {external_id}"
        ranges = split_parts(text)
        if len(ranges) == 1:
            return [{
                "content": text,
                "source_label": base_label[:512],
                "connector_external_id": external_id,
                "connector_revision": revision,
            }]
        records = []
        for index, (start, end) in enumerate(ranges, start=1):
            content = text[start:end]
            if not content.strip():
                continue
            label = part_label(base_label, index, len(ranges), start, end)
            if len(label) > 512:
                # Keep the offsets: shorten the path, never the part suffix.
                suffix = label[len(base_label):]
                label = base_label[: 512 - len(suffix)] + suffix
            records.append({
                "content": content,
                "source_label": label,
                "connector_external_id": part_external_id(external_id, index),
                "connector_revision": revision,
            })
        return records

    @staticmethod
    def _evidence_batches(records: list[dict[str, Any]]):
        batch: list[dict[str, Any]] = []
        chars = 0
        for record in records:
            size = len(record["content"])
            if batch and (len(batch) >= EVIDENCE_BATCH_RECORDS or chars + size > EVIDENCE_BATCH_CHARS):
                yield batch
                batch, chars = [], 0
            batch.append(record)
            chars += size
        if batch:
            yield batch

    @staticmethod
    def _not_indexed_document(connection: ConnectorConnection, item: Any, reason: str) -> dict[str, Any]:
        """Record a file that is not indexed: revision and reason, no content."""
        return {
            "user_id": str(connection.user_id),
            "connection_id": str(connection.connection_id),
            "provider": "github",
            "external_id": item.external_id,
            "name": item.name,
            "mime_type": not_indexed_mime(reason),
            "revision": item.revision or f"unversioned:{reason}",
            "source_url": item.url,
            "content_text": None,
            # Satisfies connector_documents_content_present without storing
            # any of the file: the row is a revision + reason marker.
            "content_base64": "",
            "content_sha256": EMPTY_SHA256,
            "size_bytes": item.size_bytes if isinstance(item.size_bytes, int) else 0,
            "provider_updated_at": None,
        }

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
