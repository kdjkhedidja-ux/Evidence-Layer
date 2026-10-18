from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import (
    EvidenceAccessDenied,
    EvidenceDataUnavailable,
    SupabaseEvidenceRepository,
)

WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE = UUID("b3000000-0000-4000-8000-000000000003")
EVIDENCE_ID = UUID("e1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def row(workspace_id: UUID = WORKSPACE, content: str = "private evidence"):
    return {
        "id": str(EVIDENCE_ID),
        "workspace_id": str(workspace_id),
        "content": content,
        "source_label": "source",
        "created_at": "2026-10-18T12:00:00Z",
        "updated_at": "2026-10-18T12:00:00+00:00",
    }


def test_evidence_repository_uses_user_token_and_workspace_filters_for_crud() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.method == "DELETE":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(EVIDENCE_ID),
                        "workspace_id": str(WORKSPACE),
                    }
                ],
            )
        if request.method == "POST":
            return httpx.Response(201, json=[row(content="created")])
        if request.method == "PATCH":
            return httpx.Response(200, json=[row(content="updated")])
        return httpx.Response(200, json=[row()])

    repository = SupabaseEvidenceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )

    async def exercise():
        listed = await repository.list_records(TOKEN, WORKSPACE)
        fetched = await repository.get_record(TOKEN, WORKSPACE, EVIDENCE_ID)
        created = await repository.create_record(
            TOKEN, WORKSPACE, content="created", source_label="source"
        )
        updated = await repository.update_record(
            TOKEN, WORKSPACE, EVIDENCE_ID, {"content": "updated"}
        )
        deleted = await repository.delete_record(TOKEN, WORKSPACE, EVIDENCE_ID)
        return listed, fetched, created, updated, deleted

    listed, fetched, created, updated, deleted = asyncio.run(exercise())

    assert len(listed) == 1 and listed[0].workspace_id == WORKSPACE
    assert fetched is not None and fetched.id == EVIDENCE_ID
    assert created is not None and created.content == "created"
    assert updated is not None and updated.content == "updated"
    assert deleted is True
    assert [request.method for request in observed] == [
        "GET", "GET", "POST", "PATCH", "DELETE"
    ]
    for request in observed:
        assert request.url.path == "/rest/v1/evidence_records"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["apikey"] == settings().publishable_key
        assert "service_role" not in request.headers["apikey"]
    assert observed[0].url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert observed[1].url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert observed[1].url.params["id"] == f"eq.{EVIDENCE_ID}"
    assert observed[2].read() == (
        f'{{"workspace_id":"{WORKSPACE}","content":"created","source_label":"source"}}'
    ).encode()
    assert observed[3].url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert observed[3].url.params["id"] == f"eq.{EVIDENCE_ID}"
    assert observed[4].url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert observed[4].url.params["id"] == f"eq.{EVIDENCE_ID}"
    assert observed[4].url.params["select"] == "id,workspace_id"


def test_delete_accepts_postgrest_single_row_representation() -> None:
    repository = SupabaseEvidenceRepository(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"id": str(EVIDENCE_ID), "workspace_id": str(WORKSPACE)}
            )
        ),
    )
    assert asyncio.run(repository.delete_record(TOKEN, WORKSPACE, EVIDENCE_ID))


def test_delete_rejects_a_representation_for_another_workspace() -> None:
    repository = SupabaseEvidenceRepository(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "id": str(EVIDENCE_ID),
                    "workspace_id": str(OTHER_WORKSPACE),
                },
            )
        ),
    )
    with pytest.raises(EvidenceDataUnavailable):
        asyncio.run(repository.delete_record(TOKEN, WORKSPACE, EVIDENCE_ID))


def test_repository_rejects_response_containing_another_workspace() -> None:
    repository = SupabaseEvidenceRepository(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[row(OTHER_WORKSPACE)])
        ),
    )
    with pytest.raises(EvidenceDataUnavailable):
        asyncio.run(repository.list_records(TOKEN, WORKSPACE))


def test_repository_maps_rls_denial_without_returning_upstream_details() -> None:
    repository = SupabaseEvidenceRepository(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                403, json={"message": "synthetic database detail"}
            )
        ),
    )
    with pytest.raises(EvidenceAccessDenied):
        asyncio.run(repository.delete_record(TOKEN, WORKSPACE, EVIDENCE_ID))


def test_delete_requires_returned_rows_and_does_not_assume_204_means_deleted() -> None:
    repository = SupabaseEvidenceRepository(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )
    with pytest.raises(EvidenceDataUnavailable):
        asyncio.run(repository.delete_record(TOKEN, WORKSPACE, EVIDENCE_ID))
