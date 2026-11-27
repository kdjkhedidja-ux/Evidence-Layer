"""Workspace-filtered evidence access through the caller's Supabase JWT."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.error_cause import mark_upstream_status

EVIDENCE_COLUMNS = (
    "id,workspace_id,content,source_label,source_id,source_version_id,"
    "created_at,updated_at"
)


class EvidenceDataUnavailable(Exception):
    """The evidence Data API request could not be completed safely."""


class EvidenceAccessDenied(Exception):
    """Postgres RLS denied access to an evidence record."""


class EvidenceSearchUnavailable(EvidenceDataUnavailable):
    """The database has no whole-workspace search yet (before 20261127120000)."""


# Full-text matches added to the newest window for retrieval (Step E).
SEARCH_CANDIDATE_LIMIT = 200


@dataclass(frozen=True)
class EvidenceRecord:
    id: UUID
    workspace_id: UUID
    content: str
    source_label: str | None
    created_at: datetime
    updated_at: datetime
    source_id: UUID | None = None
    source_version_id: UUID | None = None


class EvidenceRepository(Protocol):
    async def list_records(
        self, access_token: str, workspace_id: UUID
    ) -> list[EvidenceRecord]: ...

    async def get_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ) -> EvidenceRecord | None: ...

    async def create_record(
        self,
        access_token: str,
        workspace_id: UUID,
        *,
        content: str,
        source_label: str | None,
        source_id: UUID | None = None,
        source_version_id: UUID | None = None,
    ) -> EvidenceRecord: ...

    async def update_record(
        self,
        access_token: str,
        workspace_id: UUID,
        evidence_id: UUID,
        changes: Mapping[str, object],
    ) -> EvidenceRecord | None: ...

    async def delete_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ) -> bool: ...


class SupabaseEvidenceRepository:
    """Use the verified user's access token; never elevate to service_role."""

    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def list_records(
        self, access_token: str, workspace_id: UUID
    ) -> list[EvidenceRecord]:
        response = await self._request(
            "GET",
            access_token,
            params={
                "select": EVIDENCE_COLUMNS,
                "workspace_id": f"eq.{workspace_id}",
                "order": "created_at.desc,id.asc",
            },
        )
        return self._parse_rows(response, workspace_id)

    async def get_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ) -> EvidenceRecord | None:
        response = await self._request(
            "GET",
            access_token,
            params={
                "select": EVIDENCE_COLUMNS,
                "workspace_id": f"eq.{workspace_id}",
                "id": f"eq.{evidence_id}",
                "limit": "1",
            },
        )
        rows = self._parse_rows(response, workspace_id)
        return rows[0] if rows else None

    async def create_record(
        self,
        access_token: str,
        workspace_id: UUID,
        *,
        content: str,
        source_label: str | None,
        source_id: UUID | None = None,
        source_version_id: UUID | None = None,
    ) -> EvidenceRecord:
        response = await self._request(
            "POST",
            access_token,
            params={"select": EVIDENCE_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "content": content,
                "source_label": source_label,
                "source_id": str(source_id) if source_id else None,
                "source_version_id": str(source_version_id)
                if source_version_id
                else None,
            },
        )
        rows = self._parse_rows(response, workspace_id)
        if len(rows) != 1:
            raise EvidenceDataUnavailable
        return rows[0]

    async def update_record(
        self,
        access_token: str,
        workspace_id: UUID,
        evidence_id: UUID,
        changes: Mapping[str, object],
    ) -> EvidenceRecord | None:
        response = await self._request(
            "PATCH",
            access_token,
            params={
                "select": EVIDENCE_COLUMNS,
                "workspace_id": f"eq.{workspace_id}",
                "id": f"eq.{evidence_id}",
            },
            json=dict(changes),
        )
        rows = self._parse_rows(response, workspace_id)
        return rows[0] if rows else None

    async def delete_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ) -> bool:
        response = await self._request(
            "DELETE",
            access_token,
            params={
                "select": "id,workspace_id",
                "workspace_id": f"eq.{workspace_id}",
                "id": f"eq.{evidence_id}",
            },
        )
        return self._parse_deleted_row(response, workspace_id, evidence_id)

    async def search_records(
        self,
        access_token: str,
        workspace_id: UUID,
        query: str,
        limit: int = SEARCH_CANDIDATE_LIMIT,
    ) -> list[EvidenceRecord]:
        """Whole-workspace full-text prefilter, ranked, under the caller's RLS.

        Raises ``EvidenceSearchUnavailable`` when the database does not have
        ``public.search_workspace_evidence`` yet, so callers keep the newest
        window only.
        """
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/search_workspace_evidence"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json={
                        "p_workspace_id": str(workspace_id),
                        "p_query": query[:2000],
                        "p_limit": limit,
                    },
                )
        except httpx.HTTPError as exc:
            raise EvidenceDataUnavailable from exc
        if response.status_code == 404 and _postgrest_code(response) == "PGRST202":
            raise EvidenceSearchUnavailable
        if response.status_code == 403:
            raise EvidenceAccessDenied
        if response.status_code != 200:
            raise mark_upstream_status(EvidenceDataUnavailable(), response.status_code)
        return self._parse_rows(response, workspace_id)

    async def _request(
        self,
        method: str,
        access_token: str,
        *,
        params: dict[str, str],
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    self._settings.evidence_records_url,
                    params=params,
                    headers=headers,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise EvidenceDataUnavailable from exc
        if response.status_code == 403:
            raise EvidenceAccessDenied
        if response.status_code not in {200, 201}:
            raise mark_upstream_status(EvidenceDataUnavailable(), response.status_code)
        return response

    @classmethod
    def _parse_rows(
        cls, response: httpx.Response, expected_workspace_id: UUID
    ) -> list[EvidenceRecord]:
        payload = cls._json_list(response)
        records: list[EvidenceRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                raise EvidenceDataUnavailable
            try:
                record = EvidenceRecord(
                    id=UUID(str(item["id"])),
                    workspace_id=UUID(str(item["workspace_id"])),
                    content=item["content"],
                    source_label=item["source_label"],
                    created_at=cls._parse_datetime(item["created_at"]),
                    updated_at=cls._parse_datetime(item["updated_at"]),
                    source_id=cls._parse_optional_uuid(item["source_id"]),
                    source_version_id=cls._parse_optional_uuid(
                        item["source_version_id"]
                    ),
                )
                if (
                    record.workspace_id != expected_workspace_id
                    or not isinstance(record.content, str)
                    or (
                        record.source_label is not None
                        and not isinstance(record.source_label, str)
                    )
                    or (record.source_id is None) != (record.source_version_id is None)
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise EvidenceDataUnavailable from exc
            records.append(record)
        return records

    @staticmethod
    def _json_list(response: httpx.Response) -> list[object]:
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise EvidenceDataUnavailable from exc
        if not isinstance(payload, list):
            raise EvidenceDataUnavailable
        return payload

    @staticmethod
    def _parse_deleted_row(
        response: httpx.Response, workspace_id: UUID, evidence_id: UUID
    ) -> bool:
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise EvidenceDataUnavailable from exc
        rows = payload if isinstance(payload, list) else [payload]
        if not rows:
            return False
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise EvidenceDataUnavailable
        try:
            returned_id = UUID(str(rows[0]["id"]))
            returned_workspace_id = UUID(str(rows[0]["workspace_id"]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise EvidenceDataUnavailable from exc
        if returned_id != evidence_id or returned_workspace_id != workspace_id:
            raise EvidenceDataUnavailable
        return True

    @staticmethod
    def _parse_optional_uuid(value: object) -> UUID | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError
        return UUID(value)

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed


def _postgrest_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None
