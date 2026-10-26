"""Opt-in live Storage API integration; never points at a hosted project.

Required local-only environment:
EVIDENCE_LAYER_LOCAL_STORAGE_URL, EVIDENCE_LAYER_LOCAL_STORAGE_BUCKET,
EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY. The bucket and user policy must
already allow that user to insert and select objects while rejecting overwrite.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationRejected,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
)
from evidence_service.secure_snapshot_read import (
    InMemorySnapshotCatalog,
    LocalSupabaseStorageObjectStore,
    SnapshotObjectConflict,
    SnapshotObjectUnavailable,
    SnapshotPublisher,
    SecureSnapshotReadPath,
)
from evidence_service.tenant_context import WorkspaceMembership


class LocalWorkspaceDirectory:
    def __init__(self, principal: Principal, workspace_id: UUID) -> None:
        self._principal = principal
        self._workspace_id = workspace_id

    async def ensure_personal_workspace(self, principal, access_token):
        raise AssertionError("not used")

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        if principal != self._principal or workspace_id != self._workspace_id:
            return None
        return WorkspaceMembership(workspace_id, "Local Storage workspace", "owner")


class LocalSupabaseAuthVerifier:
    """Verify local Auth tokens against the loopback Auth API's published JWKS.

    Local Supabase CLI v2 signs with an asymmetric ES256 key. The algorithm is
    fixed here (never taken from the token), the key is selected by ``kid`` from
    the loopback JWKS, and signature, issuer, audience and expiry are all
    verified locally. Auth's /user endpoint is an additional liveness check.
    """

    def __init__(
        self, api_url: str, publishable_key: str, *, audience: str = "authenticated"
    ) -> None:
        parsed = urlsplit(api_url)
        try:
            loopback = parsed.hostname == "localhost" or bool(
                parsed.hostname and ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if parsed.scheme != "http" or not loopback or not publishable_key:
            raise ValueError("integration Auth verifier accepts local URLs only")
        self._api_url = api_url.rstrip("/")
        self._publishable_key = publishable_key
        self._audience = audience

    async def _jwks_key(self, kid: object):
        if not isinstance(kid, str) or not kid:
            raise AuthenticationRejected
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._api_url}/auth/v1/.well-known/jwks.json",
                    headers={"apikey": self._publishable_key},
                )
        except httpx.HTTPError as exc:
            raise AuthenticationUnavailable from exc
        if response.status_code != 200:
            raise AuthenticationUnavailable
        try:
            return jwt.PyJWK(next(
                candidate
                for candidate in response.json().get("keys", [])
                if candidate.get("kid") == kid and candidate.get("alg") == "ES256"
            )).key
        except (StopIteration, ValueError, TypeError, jwt.PyJWTError) as exc:
            raise AuthenticationRejected from exc

    async def verified_claims(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationRejected from exc
        if header.get("typ") != "JWT" or header.get("alg") != "ES256":
            raise AuthenticationRejected
        key = await self._jwks_key(header.get("kid"))
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=["ES256"],
                audience=self._audience,
                issuer=f"{self._api_url}/auth/v1",
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationRejected from exc

    async def verify_access_token(self, token: str) -> VerifiedAccessToken:
        claims = await self.verified_claims(token)
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._api_url}/auth/v1/user",
                    headers={
                        "apikey": self._publishable_key,
                        "authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthenticationUnavailable from exc
        if response.status_code in {400, 401, 403}:
            raise AuthenticationRejected
        if response.status_code != 200:
            raise AuthenticationUnavailable

        try:
            auth_user = response.json()
            subject = UUID(auth_user["id"])
            if (
                UUID(claims["sub"]) != subject
                or claims.get("role") != "authenticated"
                or auth_user.get("is_anonymous") is True
                or type(claims.get("exp")) not in {int, float}
            ):
                raise AuthenticationRejected
            expiry = datetime.fromtimestamp(float(claims["exp"]), timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AuthenticationRejected from exc
        if expiry <= datetime.now(timezone.utc):
            raise AuthenticationRejected
        return VerifiedAccessToken(
            Principal(issuer=claims["iss"], subject=subject), expiry
        )


class LocalSourceAcl:
    async def may_read(self, principal_id, workspace_id, source_id, version_id):
        return all(type(value) is UUID for value in (principal_id, workspace_id, source_id, version_id))

_ENV = (
    "EVIDENCE_LAYER_LOCAL_STORAGE_URL",
    "EVIDENCE_LAYER_LOCAL_STORAGE_BUCKET",
    "EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY",
)
_MISSING = [name for name in _ENV if not os.environ.get(name)]


@pytest.mark.skipif(_MISSING, reason="isolated local Supabase Storage credentials are unset")
def test_real_local_storage_api_reads_exact_write_once_object():
    async def run() -> None:
        store = LocalSupabaseStorageObjectStore(
            base_url=os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_URL"],
            bucket=os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_BUCKET"],
            publishable_key=os.environ[
                "EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY"
            ],
        )
        api_url = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_URL"].rstrip("/")
        api_key = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY"]
        email = f"gate2-{uuid4()}@example.invalid"
        password = secrets.token_urlsafe(32)
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            signup = await client.post(
                f"{api_url}/auth/v1/signup",
                headers={"apikey": api_key},
                json={"email": email, "password": password},
            )
        if signup.status_code not in {200, 201}:
            pytest.fail(f"local Auth signup failed with HTTP {signup.status_code}")
        auth_payload = signup.json()
        token = auth_payload.get("access_token")
        if not isinstance(token, str) or not token:
            pytest.fail("local Auth signup returned no access token")
        verifier = LocalSupabaseAuthVerifier(api_url, api_key)
        verified = await verifier.verify_access_token(token)
        principal = verified.principal
        workspace_id, source_id, version_id = uuid4(), uuid4(), uuid4()
        original = b"local Supabase Storage integration payload"
        catalog = InMemorySnapshotCatalog()
        publisher = SnapshotPublisher(catalog=catalog, store=store)
        manifest = await publisher.publish(
            workspace_id=workspace_id,
            source_id=source_id,
            owner_id=principal.subject,
            version_id=version_id,
            content=original,
            access_token=token,
        )
        path = SecureSnapshotReadPath(
            verifier=verifier,
            workspace_directory=LocalWorkspaceDirectory(principal, workspace_id),
            catalog=catalog,
            acl=LocalSourceAcl(),
            store=store,
        )
        result = await path.read(
            AuthenticatedRequest(principal, token),
            workspace_id=workspace_id,
            source_id=source_id,
            version_id=version_id,
        )
        assert result.content == original
        assert result.manifest == manifest

        # Existing keys must not be replaced by the ordinary local identity.
        with pytest.raises((SnapshotObjectConflict, SnapshotObjectUnavailable)):
            await store.put_if_absent(
                manifest.object_key,
                b"replacement must be rejected",
                access_token=token,
            )
        assert await store.read_exact(manifest.object_key, access_token=token) == original

    asyncio.run(run())


_SIGNING_ENV = _ENV + ("EVIDENCE_LAYER_LOCAL_AUTH_SIGNING_JWK_PATH",)
_SIGNING_MISSING = [name for name in _SIGNING_ENV if not os.environ.get(name)]


@pytest.mark.skipif(
    _SIGNING_MISSING, reason="local Auth signing key for the isolated stack is unset"
)
def test_local_auth_jwks_verification_accepts_only_valid_es256_tokens():
    async def run() -> None:
        api_url = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_URL"].rstrip("/")
        api_key = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY"]
        with open(os.environ["EVIDENCE_LAYER_LOCAL_AUTH_SIGNING_JWK_PATH"]) as handle:
            jwk = json.load(handle)[0]
        signing_key = jwt.PyJWK(jwk).key
        kid = jwk["kid"]
        verifier = LocalSupabaseAuthVerifier(api_url, api_key)
        now = int(time.time())
        claims = {
            "iss": f"{api_url}/auth/v1",
            "aud": "authenticated",
            "role": "authenticated",
            "sub": str(uuid4()),
            "exp": now + 300,
        }

        def sign(payload, key=signing_key, key_id=kid, algorithm="ES256"):
            return jwt.encode(
                payload, key, algorithm=algorithm, headers={"kid": key_id, "typ": "JWT"}
            )

        verified = await verifier.verified_claims(sign(claims))
        assert verified["sub"] == claims["sub"]

        attacker = ec.generate_private_key(ec.SECP256R1())
        without_exp = {k: v for k, v in claims.items() if k != "exp"}
        rejected = {
            "forged signature, real kid": sign(claims, key=attacker),
            "unknown kid": sign(claims, key=attacker, key_id="unknown-kid"),
            "wrong issuer": sign({**claims, "iss": "http://127.0.0.1:1/auth/v1"}),
            "wrong audience": sign({**claims, "aud": "anon"}),
            "expired": sign({**claims, "exp": now - 60}),
            "missing expiry": sign(without_exp),
            "token-selected HS256": sign(claims, key="x" * 32, algorithm="HS256"),
        }
        for name, token in rejected.items():
            with pytest.raises(AuthenticationRejected):
                await verifier.verified_claims(token)
            assert name

    asyncio.run(run())
