"""Workspace-scoped, user-token access to explicit evidence relationships."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings

RELATIONSHIP_COLUMNS = (
    "id,workspace_id,evidence_id_a,evidence_id_b,relation_type,rationale,"
    "provenance_note,created_by,created_at,updated_by,updated_at"
)


class RelationshipDataUnavailable(Exception):
    """The relationship Data API request could not be completed safely."""


class RelationshipAccessDenied(Exception):
    """RLS denied access to a relationship."""


class RelationshipConflict(Exception):
    """The relationship conflicts with existing workspace data."""


@dataclass(frozen=True)
class EvidenceRelationship:
    id: UUID
    workspace_id: UUID
    evidence_id_a: UUID
    evidence_id_b: UUID
    relation_type: str
    rationale: str
    provenance_note: str
    created_by: UUID
    created_at: datetime
    updated_by: UUID
    updated_at: datetime


class RelationshipRepository(Protocol):
    async def list_relationships(
        self, access_token: str, workspace_id: UUID
    ) -> list[EvidenceRelationship]: ...

    async def create_relationship(
        self,
        access_token: str,
        workspace_id: UUID,
        *,
        evidence_id_a: UUID,
        evidence_id_b: UUID,
        relation_type: str,
        rationale: str,
        provenance_note: str,
    ) -> EvidenceRelationship: ...

    async def update_relationship(
        self,
        access_token: str,
        workspace_id: UUID,
        relationship_id: UUID,
        changes: Mapping[str, object],
    ) -> EvidenceRelationship | None: ...

    async def delete_relationship(
        self, access_token: str, workspace_id: UUID, relationship_id: UUID
    ) -> bool: ...


class SupabaseRelationshipRepository:
    """Use only the authenticated user's token; RLS remains authoritative."""

    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def list_relationships(
        self, access_token: str, workspace_id: UUID
    ) -> list[EvidenceRelationship]:
        response = await self._request(
            "GET",
            access_token,
            params={
                "select": RELATIONSHIP_COLUMNS,
                "workspace_id": f"eq.{workspace_id}",
                "order": "created_at.desc,id.asc",
            },
        )
        return self._parse_rows(response, workspace_id)

    async def create_relationship(
        self,
        access_token: str,
        workspace_id: UUID,
        *,
        evidence_id_a: UUID,
        evidence_id_b: UUID,
        relation_type: str,
        rationale: str,
        provenance_note: str,
    ) -> EvidenceRelationship:
        response = await self._request(
            "POST",
            access_token,
            params={"select": RELATIONSHIP_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "evidence_id_a": str(evidence_id_a),
                "evidence_id_b": str(evidence_id_b),
                "relation_type": relation_type,
                "rationale": rationale,
                "provenance_note": provenance_note,
            },
        )
        rows = self._parse_rows(response, workspace_id)
        if len(rows) != 1:
            raise RelationshipDataUnavailable
        return rows[0]

    async def update_relationship(
        self,
        access_token: str,
        workspace_id: UUID,
        relationship_id: UUID,
        changes: Mapping[str, object],
    ) -> EvidenceRelationship | None:
        response = await self._request(
            "PATCH",
            access_token,
            params={
                "select": RELATIONSHIP_COLUMNS,
                "workspace_id": f"eq.{workspace_id}",
                "id": f"eq.{relationship_id}",
            },
            json=dict(changes),
        )
        rows = self._parse_rows(response, workspace_id)
        return rows[0] if rows else None

    async def delete_relationship(
        self, access_token: str, workspace_id: UUID, relationship_id: UUID
    ) -> bool:
        response = await self._request(
            "DELETE",
            access_token,
            params={
                "select": "id,workspace_id",
                "workspace_id": f"eq.{workspace_id}",
                "id": f"eq.{relationship_id}",
            },
        )
        rows = self._json_list(response)
        if any(
            not isinstance(row, dict)
            or row.get("workspace_id") != str(workspace_id)
            or row.get("id") != str(relationship_id)
            for row in rows
        ):
            raise RelationshipDataUnavailable
        return bool(rows)

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
                    self._settings.evidence_relationships_url,
                    params=params,
                    headers=headers,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise RelationshipDataUnavailable from exc
        if response.status_code == 403:
            raise RelationshipAccessDenied
        if response.status_code == 409:
            raise RelationshipConflict
        if response.status_code not in {200, 201}:
            raise RelationshipDataUnavailable
        return response

    @classmethod
    def _parse_rows(
        cls, response: httpx.Response, expected_workspace_id: UUID
    ) -> list[EvidenceRelationship]:
        rows = cls._json_list(response)
        parsed: list[EvidenceRelationship] = []
        for item in rows:
            if not isinstance(item, dict):
                raise RelationshipDataUnavailable
            try:
                relationship = EvidenceRelationship(
                    id=UUID(str(item["id"])),
                    workspace_id=UUID(str(item["workspace_id"])),
                    evidence_id_a=UUID(str(item["evidence_id_a"])),
                    evidence_id_b=UUID(str(item["evidence_id_b"])),
                    relation_type=item["relation_type"],
                    rationale=item["rationale"],
                    provenance_note=item["provenance_note"],
                    created_by=UUID(str(item["created_by"])),
                    created_at=cls._parse_datetime(item["created_at"]),
                    updated_by=UUID(str(item["updated_by"])),
                    updated_at=cls._parse_datetime(item["updated_at"]),
                )
                if (
                    relationship.workspace_id != expected_workspace_id
                    or relationship.evidence_id_a >= relationship.evidence_id_b
                    or relationship.relation_type
                    not in {"supports", "contradicts", "contextualizes", "derived_from"}
                    or not isinstance(relationship.rationale, str)
                    or not isinstance(relationship.provenance_note, str)
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise RelationshipDataUnavailable from exc
            parsed.append(relationship)
        return parsed

    @staticmethod
    def _json_list(response: httpx.Response) -> list[object]:
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise RelationshipDataUnavailable from exc
        if not isinstance(payload, list):
            raise RelationshipDataUnavailable
        return payload

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
