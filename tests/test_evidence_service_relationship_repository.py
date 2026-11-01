from __future__ import annotations

import asyncio
import json
from uuid import UUID

import httpx
import pytest

from evidence_service.config import SupabaseSettings
from evidence_service.relationship_repository import (
    RelationshipAccessDenied,
    RelationshipConflict,
    RelationshipDataUnavailable,
    SupabaseRelationshipRepository,
)

WORKSPACE_ID = UUID("a1000000-0000-4000-8000-000000000001")
EVIDENCE_A = UUID("e1000000-0000-4000-8000-000000000001")
EVIDENCE_B = UUID("e2000000-0000-4000-8000-000000000002")
RELATION_ID = UUID("f1000000-0000-4000-8000-000000000001")
CREATOR_ID = UUID("10000000-0000-4000-8000-000000000001")
TOKEN = "user.access.token"


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def response_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(RELATION_ID),
        "workspace_id": str(WORKSPACE_ID),
        "evidence_id_a": str(EVIDENCE_A),
        "evidence_id_b": str(EVIDENCE_B),
        "relation_type": "contextualizes",
        "rationale": "The records provide related context.",
        "provenance_note": "Manual comparison of both original records.",
        "created_by": str(CREATOR_ID),
        "created_at": "2026-11-02T12:00:00Z",
        "updated_by": str(CREATOR_ID),
        "updated_at": "2026-11-02T12:00:00Z",
    }
    row.update(changes)
    return row


def test_repository_lists_with_the_user_token_and_validates_workspace_rows() -> None:
    async def run() -> None:
        captured: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[response_row()])

        repository = SupabaseRelationshipRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        relationships = await repository.list_relationships(TOKEN, WORKSPACE_ID)

        assert len(relationships) == 1
        assert relationships[0].id == RELATION_ID
        assert captured[0].url.path == "/rest/v1/evidence_relationships"
        assert captured[0].headers["authorization"] == f"Bearer {TOKEN}"
        assert captured[0].headers["apikey"] == settings().publishable_key
        assert captured[0].url.params.get("workspace_id") == f"eq.{WORKSPACE_ID}"

    asyncio.run(run())


def test_repository_create_sends_only_mutable_relationship_fields() -> None:
    async def run() -> None:
        captured: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json=[response_row()])

        repository = SupabaseRelationshipRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        created = await repository.create_relationship(
            TOKEN,
            WORKSPACE_ID,
            evidence_id_a=EVIDENCE_A,
            evidence_id_b=EVIDENCE_B,
            relation_type="contextualizes",
            rationale="The records provide related context.",
            provenance_note="Manual comparison of both original records.",
        )

        payload = json.loads(captured[0].content)
        assert created.workspace_id == WORKSPACE_ID
        assert payload == {
            "workspace_id": str(WORKSPACE_ID),
            "evidence_id_a": str(EVIDENCE_A),
            "evidence_id_b": str(EVIDENCE_B),
            "relation_type": "contextualizes",
            "rationale": "The records provide related context.",
            "provenance_note": "Manual comparison of both original records.",
        }
        assert "created_by" not in payload and "updated_by" not in payload

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status_code", "exception"),
    [(403, RelationshipAccessDenied), (409, RelationshipConflict), (503, RelationshipDataUnavailable)],
)
def test_repository_maps_denied_conflict_and_service_failures(status_code, exception) -> None:
    async def run() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"message": "provider details"})

        repository = SupabaseRelationshipRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        with pytest.raises(exception):
            await repository.list_relationships(TOKEN, WORKSPACE_ID)

    asyncio.run(run())


def test_repository_rejects_malformed_and_cross_workspace_rows() -> None:
    async def run() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[response_row(workspace_id="b3000000-0000-4000-8000-000000000003")],
            )

        repository = SupabaseRelationshipRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        with pytest.raises(RelationshipDataUnavailable):
            await repository.list_relationships(TOKEN, WORKSPACE_ID)

    asyncio.run(run())


def test_repository_rejects_naive_timestamps() -> None:
    async def run() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[response_row(created_at="2026-11-02T12:00:00")])

        repository = SupabaseRelationshipRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        with pytest.raises(RelationshipDataUnavailable):
            await repository.list_relationships(TOKEN, WORKSPACE_ID)

    asyncio.run(run())
