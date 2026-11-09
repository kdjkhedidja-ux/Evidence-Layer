from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.supabase_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceDataUnavailable,
)
from evidence_service.tenant_context import WorkspaceAccessDenied


SUBJECT = uuid.UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = uuid.UUID("a1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def test_membership_query_uses_verified_user_token_and_no_admin_key() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "user_id": str(SUBJECT),
                    "workspace_id": str(WORKSPACE),
                    "role": "owner",
                    "workspace": {"id": str(WORKSPACE), "name": "A primary"},
                }
            ],
        )

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(
        repository.list_memberships(Principal(settings().jwt_issuer, SUBJECT), TOKEN)
    )

    request = observed[0]
    assert request.url.path == "/rest/v1/workspace_memberships"
    assert request.url.params["user_id"] == f"eq.{SUBJECT}"
    assert request.url.params["status"] == "eq.active"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["apikey"] == settings().publishable_key
    assert "service_role" not in request.headers["apikey"]
    assert [(item.workspace_id, item.role) for item in result] == [(WORKSPACE, "owner")]


def test_personal_workspace_rpc_uses_user_token_without_identity_input() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "workspace_id": str(WORKSPACE),
                    "workspace_name": "Personal workspace",
                    "user_id": str(SUBJECT),
                    "role": "owner",
                }
            ],
        )

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(
        repository.ensure_personal_workspace(
            Principal(settings().jwt_issuer, SUBJECT), TOKEN
        )
    )

    request = observed[0]
    assert request.method == "POST"
    assert request.url.path == "/rest/v1/rpc/ensure_personal_workspace"
    assert json.loads(request.content) == {}
    assert "user_id" not in json.loads(request.content)
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["apikey"] == settings().publishable_key
    assert "service_role" not in request.headers["apikey"]
    assert result.workspace_id == WORKSPACE
    assert result.workspace_name == "Personal workspace"
    assert result.role == "owner"


def test_personal_workspace_rpc_rejects_a_different_claimed_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "workspace_id": str(WORKSPACE),
                    "workspace_name": "Personal workspace",
                    "user_id": "20000000-0000-4000-8000-000000000002",
                    "role": "owner",
                }
            ],
        )

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(WorkspaceDataUnavailable):
        asyncio.run(
            repository.ensure_personal_workspace(
                Principal(settings().jwt_issuer, SUBJECT), TOKEN
            )
        )


@pytest.mark.parametrize(
    ("status_code", "error_code", "expected_error"),
    [
        (401, "PGRST301", AuthenticationRejected),
        (401, "PT401", AuthenticationRejected),
        (403, "PT403", WorkspaceAccessDenied),
        (503, "PGRST000", WorkspaceDataUnavailable),
        # A status or generic SQL privilege code alone is not an authorization signal.
        (401, None, WorkspaceDataUnavailable),
        (403, "42501", WorkspaceDataUnavailable),
    ],
)
def test_personal_workspace_rpc_classifies_only_trusted_error_codes(
    status_code: int,
    error_code: str | None,
    expected_error: type[Exception],
) -> None:
    body = {"message": "synthetic provider detail"}
    if error_code is not None:
        body["code"] = error_code

    repository = SupabaseWorkspaceRepository(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=body)
        ),
    )

    with pytest.raises(expected_error):
        asyncio.run(
            repository.ensure_personal_workspace(
                Principal(settings().jwt_issuer, SUBJECT), TOKEN
            )
        )


def test_membership_repository_rejects_rows_for_a_different_subject() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "user_id": "20000000-0000-4000-8000-000000000002",
                    "workspace_id": str(WORKSPACE),
                    "role": "owner",
                    "workspace": {"id": str(WORKSPACE), "name": "A primary"},
                }
            ],
        )

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(WorkspaceDataUnavailable):
        asyncio.run(
            repository.list_memberships(
                Principal(settings().jwt_issuer, SUBJECT), TOKEN
            )
        )


def test_get_account_defaults_to_individual_and_uses_user_token() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        assert request.url.path == "/rest/v1/accounts"
        assert request.url.params["user_id"] == f"eq.{SUBJECT}"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["apikey"] == settings().publishable_key
        return httpx.Response(200, json=[])

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    principal = Principal(settings().jwt_issuer, SUBJECT)
    profile = asyncio.run(repository.get_account(principal, TOKEN))
    assert profile.account_type == "individual"
    assert profile.organization_name is None
    assert len(observed) == 1


def test_create_organization_account_posts_rpc_and_parses_profile() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        assert request.url.path == "/rest/v1/rpc/create_organization_account"
        assert json.loads(request.content) == {"p_name": "Acme Evidence"}
        return httpx.Response(
            200,
            json={"account_type": "organization", "organization_name": "Acme Evidence"},
        )

    repository = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    principal = Principal(settings().jwt_issuer, SUBJECT)
    profile = asyncio.run(
        repository.create_organization_account(principal, TOKEN, "Acme Evidence")
    )
    assert profile.account_type == "organization"
    assert profile.organization_name == "Acme Evidence"
    assert len(observed) == 1
