from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from evidence_service.config import SupabaseSettings
from evidence_service.organization_repository import (
    OrganizationAccessDenied,
    OrganizationDataUnavailable,
    OrganizationMemberNotFound,
    OrganizationValidationError,
    SupabaseOrganizationRepository,
)


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def test_overview_calls_rpc_with_verified_user_token() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        assert request.url.path == "/rest/v1/rpc/organization_overview"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"id": "org", "name": "Acme", "members": [], "workspaces": []})

    repository = SupabaseOrganizationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(repository.overview("synthetic-token"))
    assert result["name"] == "Acme"
    assert len(observed) == 1


def test_add_member_posts_the_expected_rpc_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "p_email": "member@example.test",
            "p_role": "member",
        }
        return httpx.Response(200, json={"user_id": "member", "email": "member@example.test", "role": "member"})

    repository = SupabaseOrganizationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(
        repository.add_member("synthetic-token", "member@example.test", "member")
    )
    assert result["role"] == "member"


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (403, "42501", OrganizationAccessDenied),
        (404, "P0002", OrganizationMemberNotFound),
        (422, "22023", OrganizationValidationError),
        (500, "XX000", OrganizationDataUnavailable),
    ],
)
def test_rpc_errors_map_to_typed_failures(status, code, error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": code, "message": "synthetic"})

    repository = SupabaseOrganizationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(error):
        asyncio.run(repository.overview("synthetic-token"))


def test_delete_group_workspace_posts_the_workspace_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/delete_group_workspace"
        assert json.loads(request.content) == {
            "p_workspace_id": "30000000-0000-4000-8000-000000000001"
        }
        return httpx.Response(200, json={
            "workspace_id": "30000000-0000-4000-8000-000000000001",
            "workspace_deleted": True,
            "snapshots_deleted": 0,
            "storage_cleanup_queued": 0,
        })

    repository = SupabaseOrganizationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(
        repository.delete_group_workspace(
            "synthetic-token", "30000000-0000-4000-8000-000000000001"
        )
    )
    assert result["workspace_deleted"] is True
