"""Owner-scoped staging of source snapshot versions.

The browser uploads the object after this repository creates the staged catalog
row. Publication is intentionally out of scope: only the allowlist-scoped
snapshot verifier may move a version from ``staged`` to ``published``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class SourceStageForbidden(Exception):
    pass


class SourceStageConflict(Exception):
    pass


class SourceStageRejected(Exception):
    pass


class SourceStageUnavailable(Exception):
    pass


class StagedSource:
    def __init__(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
        object_key: str,
        sha256: str,
        size_bytes: int,
        created_at: datetime,
    ) -> None:
        self.workspace_id = workspace_id
        self.source_id = source_id
        self.version_id = version_id
        self.object_key = object_key
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.created_at = created_at


def _parse_created_at(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceStageUnavailable
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise SourceStageUnavailable from exc
    if parsed.tzinfo is None:
        raise SourceStageUnavailable
    return parsed.astimezone(timezone.utc)


def _parse_row(
    row: object,
    *,
    workspace_id: UUID,
    source_id: UUID,
    version_id: UUID,
    object_key: str,
    sha256: str,
    size_bytes: int,
) -> StagedSource:
    if not isinstance(row, dict):
        raise SourceStageUnavailable
    try:
        if (
            UUID(str(row["workspace_id"])) != workspace_id
            or UUID(str(row["source_id"])) != source_id
            or UUID(str(row["version_id"])) != version_id
            or row["object_key"] != object_key
            or row["sha256"] != sha256
            or row["size_bytes"] != size_bytes
            or row["state"] != "staged"
        ):
            raise SourceStageUnavailable
        created_at = _parse_created_at(row["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceStageUnavailable from exc
    return StagedSource(
        workspace_id=workspace_id,
        source_id=source_id,
        version_id=version_id,
        object_key=object_key,
        sha256=sha256,
        size_bytes=size_bytes,
        created_at=created_at,
    )


class SupabaseSourceStagingRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def stage(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
        object_key: str,
        sha256: str,
        size_bytes: int,
    ) -> StagedSource:
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or any(
                type(value) is not UUID
                for value in (workspace_id, source_id, version_id)
            )
            or not isinstance(object_key, str)
            or not object_key
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or type(size_bytes) is not int
            or size_bytes < 0
        ):
            raise SourceStageForbidden
        payload = {
            "workspace_id": str(workspace_id),
            "source_id": str(source_id),
            "version_id": str(version_id),
            "object_key": object_key,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "state": "staged",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    self._settings.source_snapshots_url,
                    json=payload,
                    headers={
                        "apikey": self._settings.publishable_key,
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation",
                    },
                )
        except httpx.HTTPError as exc:
            raise SourceStageUnavailable from exc
        if response.status_code in {401, 403}:
            raise SourceStageForbidden
        if response.status_code == 409:
            raise SourceStageConflict
        if response.status_code in {400, 422}:
            raise SourceStageRejected
        if response.status_code not in {200, 201}:
            raise SourceStageUnavailable
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceStageUnavailable from exc
        if not isinstance(rows, list) or len(rows) != 1:
            raise SourceStageUnavailable
        return _parse_row(
            rows[0],
            workspace_id=workspace_id,
            source_id=source_id,
            version_id=version_id,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
        )

    def staged_source_any(self, row: object) -> Any:
        # Kept for symmetry with other repositories; staging never reads rows.
        del row
        raise SourceStageUnavailable
