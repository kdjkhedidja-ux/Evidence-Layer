from __future__ import annotations

import asyncio
import builtins
import io
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import Request

from evidence_service.app import (
    _trusted_source_context_issuer,
    _trusted_source_read_boundary,
    create_app,
)
from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
)
from evidence_service.config import SupabaseSettings
from evidence_service.source_read_boundary import AsyncTrustedSourceReadBoundary
from evidence_service.source_context import TrustedSourceContextIssuer
from evidence_service.tenant_context import WorkspaceMembership
from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadIntent,
)

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT_A = UUID("10000000-0000-4000-8000-000000000001")
SUBJECT_B = UUID("20000000-0000-4000-8000-000000000002")
WORKSPACE_A = UUID("a1000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("b3000000-0000-4000-8000-000000000003")
TOKEN = "synthetic-secret-bearer-for-tests"
SOURCE_ID = "synthetic-source-id"


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer=ISSUER,
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeVerifier:
    def __init__(
        self,
        *,
        principal: Principal | None = None,
        expires_at: datetime,
    ) -> None:
        self.principal = principal or Principal(ISSUER, SUBJECT_A)
        self.expires_at = expires_at
        self.calls = 0
        self.fail_on_call: int | None = None
        self.failure: Exception = AuthenticationUnavailable()

    async def verify(self, token: str) -> Principal:
        assert token == TOKEN
        return self.principal

    async def verify_access_token(self, token: str) -> VerifiedAccessToken:
        assert token == TOKEN
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise self.failure
        return VerifiedAccessToken(self.principal, self.expires_at)


class MutableWorkspaceDirectory:
    def __init__(self) -> None:
        self.active = True
        self.fail = False
        self.calls: list[tuple[Principal, str, UUID]] = []

    async def ensure_personal_workspace(self, principal, access_token):
        raise AssertionError("not used")

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(
        self, principal: Principal, access_token: str, workspace_id: UUID
    ) -> WorkspaceMembership | None:
        self.calls.append((principal, access_token, workspace_id))
        if self.fail:
            raise RuntimeError("synthetic directory failure")
        if (
            self.active
            and principal.subject == SUBJECT_A
            and workspace_id == WORKSPACE_A
        ):
            return WorkspaceMembership(WORKSPACE_A, "A workspace", "owner")
        return None


def build_boundary(*, token_lifetime: timedelta = timedelta(minutes=2)):
    clock = MutableClock(datetime.now(timezone.utc))
    verifier = FakeVerifier(expires_at=clock.value + token_lifetime)
    directory = MutableWorkspaceDirectory()
    issuer = TrustedSourceContextIssuer(verifier, directory, clock=clock)
    context = asyncio.run(issuer.issue(TOKEN, WORKSPACE_A))
    auth = AuthenticatedRequest(Principal(ISSUER, SUBJECT_A), TOKEN)
    boundary = AsyncTrustedSourceReadBoundary(
        verifier,
        directory,
        authenticated_request=auth,
        workspace_id=WORKSPACE_A,
        clock=clock,
    )
    intent = SourceReadIntent(SOURCE_ID, "retrieve")
    return clock, verifier, directory, issuer, context, auth, boundary, intent


def forbid_content_opens(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def guarded_open(file, *args, **kwargs):
        attempts.append(file)
        raise AssertionError("source content must not be opened in phase 2")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_open)
    return attempts


def test_app_composes_request_bound_boundary_from_same_server_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = datetime.now(timezone.utc)
    verifier = FakeVerifier(expires_at=clock + timedelta(minutes=2))
    directory = MutableWorkspaceDirectory()
    app = create_app(
        settings(), token_verifier=verifier, workspace_directory=directory
    )
    request = Request({"type": "http", "app": app, "headers": []})
    issuer = _trusted_source_context_issuer(request)
    context = asyncio.run(issuer.issue(TOKEN, WORKSPACE_A))
    auth = AuthenticatedRequest(Principal(ISSUER, SUBJECT_A), TOKEN)
    boundary = _trusted_source_read_boundary(request, auth, WORKSPACE_A)
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [SourceReadIntent(SOURCE_ID, "retrieve")]))

    assert isinstance(result, SourceReadDenial)
    assert result.reason is SourceReadDenialReason.DENY_ALL
    assert boundary._verifier is app.state.token_verifier
    assert boundary._directory is app.state.workspace_directory
    assert issuer._verifier is boundary._verifier
    assert issuer._directory is boundary._directory
    assert verifier.calls == 2
    assert len(directory.calls) == 2
    assert opens == []
    assert TOKEN not in repr(boundary)


def test_revoked_membership_after_context_issue_denies_before_content_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, verifier, directory, _, context, _, boundary, intent = build_boundary()
    directory.active = False
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.REVOKED
    assert verifier.calls == 2
    assert len(directory.calls) == 2
    assert opens == []


def test_token_expired_after_context_issue_denies_before_content_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, verifier, directory, _, context, _, boundary, intent = build_boundary(
        token_lifetime=timedelta(seconds=30)
    )
    clock.value = context.expires_at + timedelta(seconds=1)
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.EXPIRED
    assert verifier.calls == 2
    assert len(directory.calls) == 1
    assert opens == []


def test_expired_context_is_rechecked_even_while_token_remains_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, verifier, directory, _, context, _, boundary, intent = build_boundary()
    clock.value = context.expires_at + timedelta(seconds=1)
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.EXPIRED
    assert verifier.calls == 2
    assert len(directory.calls) == 1
    assert opens == []


def test_verification_service_failure_denies_before_membership_or_content_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, verifier, directory, _, context, _, boundary, intent = build_boundary()
    verifier.fail_on_call = 2
    verifier.failure = AuthenticationUnavailable()
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.STORAGE_FAILURE
    assert verifier.calls == 2
    assert len(directory.calls) == 1
    assert opens == []


def test_context_workspace_must_match_the_server_selected_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, verifier, directory, _, context, auth, _, intent = build_boundary()
    boundary = AsyncTrustedSourceReadBoundary(
        verifier,
        directory,
        authenticated_request=auth,
        workspace_id=WORKSPACE_B,
    )
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.SCOPE_MISMATCH
    assert verifier.calls == 2
    assert len(directory.calls) == 1
    assert opens == []


def test_changed_verified_identity_denies_before_membership_or_content_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, verifier, directory, _, context, _, boundary, intent = build_boundary()
    verifier.principal = Principal(ISSUER, SUBJECT_B)
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.SCOPE_MISMATCH
    assert verifier.calls == 2
    assert len(directory.calls) == 1
    assert opens == []


def test_membership_directory_failure_denies_before_content_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, verifier, directory, _, context, _, boundary, intent = build_boundary()
    directory.fail = True
    opens = forbid_content_opens(monkeypatch)

    result = asyncio.run(boundary.open_sources(context, [intent]))

    assert result.reason is SourceReadDenialReason.STORAGE_FAILURE
    assert verifier.calls == 2
    assert len(directory.calls) == 2
    assert opens == []
