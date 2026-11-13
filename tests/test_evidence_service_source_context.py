from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from jwt.utils import to_base64url_uint

from evidence_service.app import (
    SourceContextDependencyUnavailable,
    _trusted_source_context_issuer,
    create_app,
)
from evidence_service.auth import (
    Principal,
    SupabaseTokenVerifier,
    VerifiedAccessToken,
)
from evidence_service.config import SupabaseSettings
from evidence_service.source_context import (
    DEFAULT_SOURCE_CONTEXT_TTL,
    MAX_SOURCE_CONTEXT_TTL,
    SourceContextDenied,
    SourceContextUnavailable,
    TrustedSourceContextIssuer,
)
from evidence_service.supabase_repository import SupabaseWorkspaceRepository
from evidence_service.tenant_context import (
    WorkspaceAccessDenied,
    WorkspaceMembership,
    resolve_workspace_context,
)

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT_A = uuid.UUID("10000000-0000-4000-8000-000000000001")
SUBJECT_B = uuid.UUID("20000000-0000-4000-8000-000000000002")
WORKSPACE_A = uuid.UUID("a1000000-0000-4000-8000-000000000001")
WORKSPACE_B = uuid.UUID("b3000000-0000-4000-8000-000000000003")


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer=ISSUER,
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(key: rsa.RSAPrivateKey) -> dict[str, object]:
    public = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "key_ops": ["verify"],
        "alg": "RS256",
        "kid": "source-context-test-key",
        "n": to_base64url_uint(public.n).decode("ascii"),
        "e": to_base64url_uint(public.e).decode("ascii"),
    }


def signed_token(
    key: rsa.RSAPrivateKey,
    *,
    subject: uuid.UUID = SUBJECT_A,
    expiry: int | None = None,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": str(subject),
            "role": "authenticated",
            "is_anonymous": False,
            "aal": "aal2",
            "iat": now,
            "exp": expiry if expiry is not None else now + 300,
        },
        key,
        algorithm="RS256",
        headers={"kid": "source-context-test-key", "typ": "JWT"},
    )


def membership_row(
    *,
    user_id: uuid.UUID = SUBJECT_A,
    workspace_id: uuid.UUID = WORKSPACE_A,
    role: str = "owner",
) -> dict[str, object]:
    return {
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "role": role,
        "workspace": {"id": str(workspace_id), "name": "A workspace"},
    }


def issuer_for(
    key: rsa.RSAPrivateKey,
    *,
    membership_payload: list[dict[str, object]] | None = None,
    membership_status: int = 200,
    verifier_status: int = 200,
    clock=None,
    ttl: timedelta | None = None,
):
    observed: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url == httpx.URL(settings().jwks_url):
            if verifier_status != 200:
                return httpx.Response(verifier_status, json={"error": "synthetic"})
            return httpx.Response(200, json={"keys": [jwk_for(key)]})
        if request.url.path == "/rest/v1/workspace_memberships":
            if membership_status != 200:
                return httpx.Response(membership_status, json={"error": "synthetic"})
            return httpx.Response(200, json=membership_payload or [])
        raise AssertionError(f"unexpected local test request: {request.url}")

    verifier = SupabaseTokenVerifier(
        settings(), transport=httpx.MockTransport(transport)
    )
    directory = SupabaseWorkspaceRepository(
        settings(), transport=httpx.MockTransport(transport)
    )
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if ttl is not None:
        kwargs["ttl"] = ttl
    return TrustedSourceContextIssuer(verifier, directory, **kwargs), observed


def test_issuer_derives_identity_from_verified_token_and_active_membership():
    key = signing_key()
    token = signed_token(key)
    fixed_now = datetime.now(timezone.utc)
    issuer, observed = issuer_for(
        key,
        membership_payload=[membership_row()],
        clock=lambda: fixed_now,
    )

    context = asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert context.principal_id == str(SUBJECT_A)
    assert context.tenant_id == str(WORKSPACE_A)
    assert context.policy_version
    assert context.expires_at == fixed_now + DEFAULT_SOURCE_CONTEXT_TTL
    assert [request.url.path for request in observed] == [
        "/auth/v1/.well-known/jwks.json",
        "/rest/v1/workspace_memberships",
    ]
    membership_request = observed[1]
    assert membership_request.url.params["user_id"] == f"eq.{SUBJECT_A}"
    assert membership_request.url.params["workspace_id"] == f"eq.{WORKSPACE_A}"
    assert membership_request.url.params["status"] == "eq.active"
    assert membership_request.headers["authorization"] == f"Bearer {token}"
    assert membership_request.headers["apikey"] == settings().publishable_key


def test_group_workspace_admin_membership_can_issue_source_context():
    key = signing_key()
    token = signed_token(key)
    issuer, _ = issuer_for(key, membership_payload=[membership_row(role="admin")])

    context = asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert context.tenant_id == str(WORKSPACE_A)


def test_app_composes_source_issuer_from_its_server_owned_verifier_and_directory():
    key = signing_key()
    token = signed_token(key)
    configured_issuer, observed = issuer_for(
        key, membership_payload=[membership_row()]
    )
    app = create_app(
        settings(),
        token_verifier=configured_issuer._verifier,
        workspace_directory=configured_issuer._directory,
    )
    request = Request({"type": "http", "app": app, "headers": []})

    source_issuer = _trusted_source_context_issuer(request)
    context = asyncio.run(source_issuer.issue(token, WORKSPACE_A))

    assert source_issuer._verifier is app.state.token_verifier
    assert source_issuer._directory is app.state.workspace_directory
    assert _trusted_source_context_issuer(request) is source_issuer
    assert app.state.trusted_source_context_issuer is source_issuer
    assert context.principal_id == str(SUBJECT_A)
    assert context.tenant_id == str(WORKSPACE_A)
    assert [request.url.path for request in observed] == [
        "/auth/v1/.well-known/jwks.json",
        "/rest/v1/workspace_memberships",
    ]


def test_principal_only_verifier_remains_usable_for_auth_but_cannot_issue_context():
    class PrincipalOnlyVerifier:
        async def verify(self, token: str) -> Principal:
            return Principal(ISSUER, SUBJECT_A)

    app = create_app(
        settings(),
        token_verifier=PrincipalOnlyVerifier(),  # type: ignore[arg-type]
        workspace_directory=object(),  # type: ignore[arg-type]
    )
    request = Request({"type": "http", "app": app, "headers": []})

    with pytest.raises(SourceContextDependencyUnavailable):
        _trusted_source_context_issuer(request)

    assert app.state.trusted_source_context_issuer is None


def test_modified_or_unverifiable_bearer_is_rejected_before_membership_lookup():
    key = signing_key()
    other_key = signing_key()
    forged_token = signed_token(other_key)
    issuer, observed = issuer_for(key, membership_payload=[membership_row()])

    with pytest.raises(SourceContextDenied):
        asyncio.run(issuer.issue(forged_token, WORKSPACE_A))

    assert [request.url.path for request in observed] == [
        "/auth/v1/.well-known/jwks.json"
    ]


def test_membership_for_another_user_is_rejected():
    key = signing_key()
    token = signed_token(key)
    issuer, observed = issuer_for(
        key, membership_payload=[membership_row(user_id=SUBJECT_B)]
    )

    with pytest.raises(SourceContextUnavailable):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert observed[-1].url.params["user_id"] == f"eq.{SUBJECT_A}"


def test_cross_workspace_membership_result_is_denied():
    key = signing_key()
    token = signed_token(key)
    issuer, observed = issuer_for(
        key, membership_payload=[membership_row(workspace_id=WORKSPACE_B)]
    )

    with pytest.raises(SourceContextDenied):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert observed[-1].url.params["workspace_id"] == f"eq.{WORKSPACE_A}"


def test_missing_or_revoked_active_membership_is_denied():
    key = signing_key()
    token = signed_token(key)
    issuer, observed = issuer_for(key, membership_payload=[])

    with pytest.raises(SourceContextDenied):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert observed[-1].url.params["status"] == "eq.active"


def test_invalid_membership_role_is_rejected():
    key = signing_key()
    token = signed_token(key)
    issuer, _ = issuer_for(key, membership_payload=[membership_row(role="superuser")])

    with pytest.raises(SourceContextUnavailable):
        asyncio.run(issuer.issue(token, WORKSPACE_A))


def test_membership_directory_failure_fails_closed():
    key = signing_key()
    token = signed_token(key)
    issuer, observed = issuer_for(
        key, membership_status=503, membership_payload=[membership_row()]
    )

    with pytest.raises(SourceContextUnavailable):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert observed[-1].url.path == "/rest/v1/workspace_memberships"


def test_verifier_failure_fails_closed_without_directory_access():
    key = signing_key()
    token = signed_token(key)
    issuer, observed = issuer_for(key, verifier_status=503)

    with pytest.raises(SourceContextUnavailable):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert len(observed) == 1
    assert observed[0].url.path == "/auth/v1/.well-known/jwks.json"


def test_context_expiry_is_capped_by_verified_token_expiry():
    key = signing_key()
    token_expiry = int(time.time()) + 45
    token = signed_token(key, expiry=token_expiry)
    fixed_now = datetime.fromtimestamp(token_expiry - 20, timezone.utc)
    issuer, _ = issuer_for(
        key,
        membership_payload=[membership_row()],
        clock=lambda: fixed_now,
        ttl=MAX_SOURCE_CONTEXT_TTL,
    )

    context = asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert context.expires_at == datetime.fromtimestamp(token_expiry, timezone.utc)


def test_expired_verified_token_cannot_issue_context():
    key = signing_key()
    token_expiry = int(time.time()) + 120
    token = signed_token(key, expiry=token_expiry)
    future = datetime.fromtimestamp(token_expiry + 1, timezone.utc)
    issuer, _ = issuer_for(
        key,
        membership_payload=[membership_row()],
        clock=lambda: future,
    )

    with pytest.raises(SourceContextDenied, match="expired"):
        asyncio.run(issuer.issue(token, WORKSPACE_A))


def test_token_expiring_during_membership_lookup_cannot_issue_context():
    key = signing_key()
    token_expiry = int(time.time()) + 120
    token = signed_token(key, expiry=token_expiry)
    clock_values = iter(
        (
            datetime.fromtimestamp(token_expiry - 1, timezone.utc),
            datetime.fromtimestamp(token_expiry + 1, timezone.utc),
        )
    )
    issuer, observed = issuer_for(
        key,
        membership_payload=[membership_row()],
        clock=lambda: next(clock_values),
    )

    with pytest.raises(SourceContextDenied, match="remaining lifetime"):
        asyncio.run(issuer.issue(token, WORKSPACE_A))

    assert observed[-1].url.path == "/rest/v1/workspace_memberships"


def test_ttl_defaults_to_one_minute_and_rejects_values_over_five_minutes():
    key = signing_key()
    issuer, _ = issuer_for(key)

    assert issuer._ttl == timedelta(minutes=1)
    assert MAX_SOURCE_CONTEXT_TTL == timedelta(minutes=5)
    with pytest.raises(ValueError, match="cannot exceed five minutes"):
        TrustedSourceContextIssuer(
            issuer._verifier,
            issuer._directory,
            ttl=timedelta(minutes=5, seconds=1),
        )
    for invalid_ttl in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError, match="positive timedelta"):
            TrustedSourceContextIssuer(
                issuer._verifier,
                issuer._directory,
                ttl=invalid_ttl,
            )


def test_malformed_verified_principal_is_rejected_before_directory_access():
    class ForgedVerifier:
        async def verify_access_token(self, access_token: str) -> VerifiedAccessToken:
            return VerifiedAccessToken(
                principal=object(),  # type: ignore[arg-type]
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )

    class MustNotReadDirectory:
        async def get_membership(self, *args, **kwargs):
            raise AssertionError("directory must not receive malformed identity")

    issuer = TrustedSourceContextIssuer(ForgedVerifier(), MustNotReadDirectory())

    with pytest.raises(SourceContextDenied, match="malformed"):
        asyncio.run(issuer.issue("signed-token-is-not-relevant", WORKSPACE_A))


def test_resolve_workspace_context_rejects_directory_cross_workspace_result():
    class CrossWorkspaceDirectory:
        async def get_membership(self, principal, access_token, workspace_id):
            assert workspace_id == WORKSPACE_A
            return WorkspaceMembership(WORKSPACE_B, "B workspace", "member")

    with pytest.raises(WorkspaceAccessDenied):
        asyncio.run(
            resolve_workspace_context(
                CrossWorkspaceDirectory(),
                Principal(ISSUER, SUBJECT_A),
                "verified-token",
                WORKSPACE_A,
            )
        )
