"""Owner-managed read grants for published source versions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class SourceGrantForbidden(Exception):
    pass


class SourceGrantConflict(Exception):
    pass


class SourceGrantRejected(Exception):
    pass


class SourceGrantUnavailable(Exception):
    pass


class SourceGrant:
    def __init__(
        self,
        *,
        principal_id: UUID,
        grant_type: str,
        granted_by: UUID,
        created_at: datetime,
    ) -> None:
        self.principal_id = principal_id
        self.grant_type = grant_type
        self.granted_by = granted_by
        self.created_at = created_at


def _parse_created_at(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceGrantUnavailable
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise SourceGrantUnavailable from exc
    if parsed.tzinfo is None:
        raise SourceGrantUnavailable
    return parsed.astimezone(timezone.utc)


def _parse_row(row: object) -> SourceGrant:
    if not isinstance(row, dict):
        raise SourceGrantUnavailable
    try:
        principal_id = UUID(str(row["principal_id"]))
        granted_by = UUID(str(row["granted_by"]))
        grant_type = row["grant_type"]
        created_at = _parse_created_at(row["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceGrantUnavailable from exc
    if grant_type != "read":
        raise SourceGrantUnavailable
    return SourceGrant(
        principal_id=principal_id,
        grant_type=grant_type,
        granted_by=granted_by,
        created_at=created_at,
    )


class SupabaseSourceGrantRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def list(
        self, access_token: str, *, workspace_id: UUID, source_id: UUID
    ) -> list[SourceGrant]:
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or type(workspace_id) is not UUID
            or type(source_id) is not UUID
        ):
            raise SourceGrantForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.get(
                    self._settings.source_grants_url,
                    params={
                        "workspace_id": f"eq.{workspace_id}",
                        "source_id": f"eq.{source_id}",
                        "select": "principal_id,grant_type,granted_by,created_at",
                        "order": "created_at.asc",
                    },
                    headers=self._headers(access_token),
                )
        except httpx.HTTPError as exc:
            raise SourceGrantUnavailable from exc
        if response.status_code in {401, 403}:
            raise SourceGrantForbidden
        if response.status_code != 200:
            raise SourceGrantUnavailable
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceGrantUnavailable from exc
        if not isinstance(rows, list):
            raise SourceGrantUnavailable
        return [_parse_row(row) for row in rows]

    async def grant(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        source_id: UUID,
        principal_id: UUID,
    ) -> SourceGrant:
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or any(
                type(value) is not UUID
                for value in (workspace_id, source_id, principal_id)
            )
        ):
            raise SourceGrantForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    self._settings.source_grants_url,
                    json={
                        "workspace_id": str(workspace_id),
                        "source_id": str(source_id),
                        "principal_id": str(principal_id),
                        "grant_type": "read",
                    },
                    headers={
                        **self._headers(access_token),
                        "Prefer": "return=representation",
                    },
                )
        except httpx.HTTPError as exc:
            raise SourceGrantUnavailable from exc
        if response.status_code in {401, 403}:
            raise SourceGrantForbidden
        if response.status_code == 409:
            raise SourceGrantConflict
        if response.status_code in {400, 422}:
            raise SourceGrantRejected
        if response.status_code not in {200, 201}:
            raise SourceGrantUnavailable
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceGrantUnavailable from exc
        if not isinstance(rows, list) or len(rows) != 1:
            raise SourceGrantUnavailable
        return _parse_row(rows[0])

    async def revoke(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        source_id: UUID,
        principal_id: UUID,
    ) -> None:
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or any(
                type(value) is not UUID
                for value in (workspace_id, source_id, principal_id)
            )
        ):
            raise SourceGrantForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.delete(
                    self._settings.source_grants_url,
                    params={
                        "workspace_id": f"eq.{workspace_id}",
                        "source_id": f"eq.{source_id}",
                        "principal_id": f"eq.{principal_id}",
                    },
                    headers=self._headers(access_token),
                )
        except httpx.HTTPError as exc:
            raise SourceGrantUnavailable from exc
        if response.status_code in {401, 403}:
            raise SourceGrantForbidden
        if response.status_code in {200, 204}:
            return
        if response.status_code == 404:
            raise SourceGrantRejected
        raise SourceGrantUnavailable
