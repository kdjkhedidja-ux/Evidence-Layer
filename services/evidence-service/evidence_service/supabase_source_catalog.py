"""User-scoped Supabase adapters for the snapshot catalog and source-grant ACL.

These adapters back the ``SnapshotCatalog`` and ``SnapshotSourceAccessPolicy``
protocols used by ``SecureSnapshotReadPath``. They are request-scoped: the verified
bearer token is bound in the constructor and is never accepted from the read-path
caller. Every request uses that token plus a publishable API key; ``service_role``
is never used.

Both tables are RLS-protected, so a row that is not visible to the requesting user
simply returns an empty result: an unreadable snapshot resolves to ``None`` and a
missing grant resolves to ``False``, which the read path turns into a denial.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.secure_snapshot_read import (
    DEFAULT_MAX_SNAPSHOT_BYTES,
    SnapshotState,
    SourceSnapshotManifest,
    snapshot_object_key,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_SELECT = (
    "workspace_id,source_id,version_id,object_key,sha256,size_bytes,state,"
    "created_by,created_at"
)


class SourceCatalogUnavailable(Exception):
    """The user-scoped snapshot catalog request could not be completed."""


class SourceAccessPolicyUnavailable(Exception):
    """The user-scoped source-grant request could not be completed."""


def _require_token(access_token: str) -> None:
    if not isinstance(access_token, str) or not access_token.strip():
        raise SourceCatalogUnavailable


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _parse_manifest(row: object, *, require_published: bool = True) -> SourceSnapshotManifest:
    if not isinstance(row, dict):
        raise SourceCatalogUnavailable
    try:
        workspace_id = UUID(str(row["workspace_id"]))
        source_id = UUID(str(row["source_id"]))
        version_id = UUID(str(row["version_id"]))
        owner_id = UUID(str(row["created_by"]))
        object_key = row["object_key"]
        sha256 = row["sha256"]
        size_bytes = row["size_bytes"]
        state = row["state"]
        created_at = _parse_utc(row["created_at"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SourceCatalogUnavailable from exc

    if (
        not isinstance(object_key, str)
        or object_key != snapshot_object_key(workspace_id, source_id, version_id)
        or not isinstance(sha256, str)
        or not _SHA256_RE.fullmatch(sha256)
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 <= size_bytes <= DEFAULT_MAX_SNAPSHOT_BYTES
        or state not in {"staged", "published"}
        or (require_published and state != "published")
    ):
        raise SourceCatalogUnavailable

    return SourceSnapshotManifest(
        workspace_id=workspace_id,
        source_id=source_id,
        owner_id=owner_id,
        version_id=version_id,
        object_key=object_key,
        sha256=sha256,
        size_bytes=size_bytes,
        state=SnapshotState(state),
        created_at=created_at,
    )


class SupabaseSnapshotCatalog:
    """PostgREST-backed catalog resolving published snapshot versions."""

    def __init__(
        self,
        settings: SupabaseSettings,
        access_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _require_token(access_token)
        self._settings = settings
        self._access_token = access_token
        self._transport = transport

    async def resolve_version(
        self, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> SourceSnapshotManifest | None:
        if (
            type(workspace_id) is not UUID
            or type(source_id) is not UUID
            or type(version_id) is not UUID
        ):
            raise SourceCatalogUnavailable
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "source_id": f"eq.{source_id}",
            "version_id": f"eq.{version_id}",
            "state": "eq.published",
            "select": _CATALOG_SELECT,
            "limit": "1",
        }
        rows = await self._request(params)
        if not rows:
            return None
        return _parse_manifest(rows[0])

    async def publish_if_absent(self, manifest: SourceSnapshotManifest) -> None:
        # Publication is owned by the allowlist-scoped verifier process, never by
        # this read-only catalog. Any call here must fail closed.
        del manifest
        raise SourceCatalogUnavailable(
            "the read-only catalog cannot publish snapshots"
        )

    async def list_visible_versions(
        self, workspace_id: UUID
    ) -> list[SourceSnapshotManifest]:
        """List the snapshot versions visible to the requesting user.

        RLS on ``source_snapshots`` restricts visibility to published versions
        of granted sources plus the user's own staged rows; the query adds no
        privilege of its own. Any malformed row fails the whole listing closed.
        """
        if type(workspace_id) is not UUID:
            raise SourceCatalogUnavailable
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "select": _CATALOG_SELECT,
            "order": "source_id.asc,created_at.desc,version_id.asc",
            "limit": "1000",
        }
        rows = await self._request(params)
        return [_parse_manifest(row, require_published=False) for row in rows]

    async def _request(self, params: dict[str, str]) -> list[object]:
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._settings.source_snapshots_url,
                    params=params,
                    headers=headers,
                )
            if response.status_code != 200:
                raise SourceCatalogUnavailable
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SourceCatalogUnavailable from exc
        if not isinstance(payload, list):
            raise SourceCatalogUnavailable
        return payload


class SupabaseSourceAccessPolicy:
    """PostgREST-backed ACL checking explicit ``source_grants`` read rows."""

    def __init__(
        self,
        settings: SupabaseSettings,
        access_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _require_token(access_token)
        self._settings = settings
        self._access_token = access_token
        self._transport = transport

    async def may_read(
        self,
        principal_id: UUID,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> bool:
        # Grants are source-scoped, so version_id is intentionally unused.
        del version_id
        if (
            type(principal_id) is not UUID
            or type(workspace_id) is not UUID
            or type(source_id) is not UUID
        ):
            raise SourceAccessPolicyUnavailable
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "source_id": f"eq.{source_id}",
            "principal_id": f"eq.{principal_id}",
            "grant_type": "eq.read",
            "select": "workspace_id",
            "limit": "1",
        }
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._settings.source_grants_url,
                    params=params,
                    headers=headers,
                )
            if response.status_code != 200:
                raise SourceAccessPolicyUnavailable
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SourceAccessPolicyUnavailable from exc
        if not isinstance(payload, list) or any(
            not isinstance(row, dict) for row in payload
        ):
            raise SourceAccessPolicyUnavailable
        return len(payload) > 0

