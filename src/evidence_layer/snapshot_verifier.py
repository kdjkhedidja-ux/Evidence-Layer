"""Verifier for staged source snapshots.

This module is shared by the standalone verifier process and its isolated unit
tests. It is not wired into the public Evidence Service, MCP, or retriever.
The verifier process uses the access token of one dedicated, allowlisted
Supabase Auth user. It never holds ``service_role``. The database allowlist and
RPCs decide whether that identity may verify; this module checks the bytes.

Flow: ``begin`` returns the owner's staged claim and the identity of the stored
Storage object; the verifier reads the bytes with a hard cap, recomputes size
and SHA-256 from those bytes, and ``publish`` presents that evidence together
with the object identity it examined. Anything unexpected fails closed.

Limits (not claimed away): the digest is computed here, so the attestation is
only as trustworthy as this process and its credential; the database compares
Storage object identity but cannot see bytes replaced beneath Storage; this is
not connected to the HTTP app, MCP or any production path.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

DEFAULT_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_VERIFY_SECONDS = 30.0
_ABSOLUTE_MAX_VERIFY_SECONDS = 120.0
_MAX_RPC_RESPONSE_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_OBJECT_VERSION_LENGTH = 512
_PUBLIC_API_KEY_PREFIX = "sb_publishable_"


def snapshot_object_key(workspace_id: UUID, source_id: UUID, version_id: UUID) -> str:
    """Return the catalog's canonical key shape (also constrained by SQL)."""
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


def _is_public_api_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_PUBLIC_API_KEY_PREFIX)
        and len(value) > len(_PUBLIC_API_KEY_PREFIX)
        and not any(character.isspace() for character in value)
    )


class VerificationFailure(str, Enum):
    INVALID_REQUEST = "invalid_request"
    NOT_AUTHORISED = "not_authorised"
    NOT_STAGED = "not_staged"
    CLAIM_INVALID = "claim_invalid"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    SIZE_MISMATCH = "size_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    PUBLISH_REJECTED = "publish_rejected"
    TIMEOUT = "timeout"


class SnapshotVerificationError(Exception):
    """Fixed, content-free failure. Never carries keys, tokens, or bytes."""

    def __init__(self, reason: VerificationFailure) -> None:
        super().__init__("snapshot verification failed")
        self.reason = reason

    def __repr__(self) -> str:
        return f"SnapshotVerificationError({self.reason.value})"


@dataclass(frozen=True)
class StagedSnapshotClaim:
    workspace_id: UUID
    source_id: UUID
    version_id: UUID
    object_key: str
    claimed_sha256: str
    claimed_size_bytes: int
    storage_object_id: UUID
    storage_object_version: str


@dataclass(frozen=True)
class VerifiedPublication:
    workspace_id: UUID
    source_id: UUID
    version_id: UUID
    sha256: str
    size_bytes: int


class VerificationGateway(Protocol):
    async def begin(
        self, access_token: str, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> StagedSnapshotClaim: ...

    async def publish(
        self, access_token: str, claim: StagedSnapshotClaim, *, sha256: str, size_bytes: int
    ) -> None: ...


class SnapshotBytesReader(Protocol):
    async def read_exact(self, object_key: str, *, access_token: str) -> bytes: ...


def _fail(reason: VerificationFailure) -> SnapshotVerificationError:
    return SnapshotVerificationError(reason)


def _valid_seconds(value: object) -> bool:
    return type(value) in (int, float) and 0 < value <= _ABSOLUTE_MAX_VERIFY_SECONDS  # type: ignore[operator]


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_service_role_token(token: str) -> bool:
    """Refuse a service_role JWT. The payload is decoded unverified and only to
    refuse; it is never used to accept or to establish identity."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(claims, dict) and claims.get("role") == "service_role"


def _valid_token(token: object) -> bool:
    return (
        isinstance(token, str)
        and bool(token.strip())
        and token == token.strip()
        and "\r" not in token
        and "\n" not in token
        and not _is_service_role_token(token)
    )


def is_valid_verifier_access_token(token: object) -> bool:
    """Return whether a runtime token is syntactically usable and not service_role."""
    return _valid_token(token)


def _check_claim(
    claim: object,
    workspace_id: UUID,
    source_id: UUID,
    version_id: UUID,
    max_bytes: int,
) -> StagedSnapshotClaim:
    if (
        type(claim) is not StagedSnapshotClaim
        or claim.workspace_id != workspace_id
        or claim.source_id != source_id
        or claim.version_id != version_id
        or claim.object_key != snapshot_object_key(workspace_id, source_id, version_id)
        or not isinstance(claim.claimed_sha256, str)
        or not _SHA256_RE.fullmatch(claim.claimed_sha256)
        or type(claim.claimed_size_bytes) is not int
        or not 0 <= claim.claimed_size_bytes <= max_bytes
        or type(claim.storage_object_id) is not UUID
        or not isinstance(claim.storage_object_version, str)
        or not 1 <= len(claim.storage_object_version) <= _MAX_OBJECT_VERSION_LENGTH
    ):
        raise _fail(VerificationFailure.CLAIM_INVALID)
    return claim


class SnapshotVerificationService:
    def __init__(
        self,
        *,
        gateway: VerificationGateway,
        reader: SnapshotBytesReader,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        max_verify_seconds: float = DEFAULT_MAX_VERIFY_SECONDS,
    ) -> None:
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
            raise ValueError("snapshot byte limit must be positive")
        if not _valid_seconds(max_verify_seconds):
            raise ValueError("verification deadline is outside the supported range")
        self._gateway = gateway
        self._reader = reader
        self._max_bytes = max_snapshot_bytes
        self._max_seconds = float(max_verify_seconds)

    async def verify_and_publish(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> VerifiedPublication:
        if (
            any(type(value) is not UUID for value in (workspace_id, source_id, version_id))
            or not _valid_token(access_token)
        ):
            raise _fail(VerificationFailure.INVALID_REQUEST)
        try:
            async with asyncio.timeout(self._max_seconds):
                return await self._run(access_token, workspace_id, source_id, version_id)
        except TimeoutError:
            raise _fail(VerificationFailure.TIMEOUT) from None

    async def _run(
        self, access_token: str, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> VerifiedPublication:
        try:
            raw_claim = await self._gateway.begin(
                access_token, workspace_id, source_id, version_id
            )
        except SnapshotVerificationError:
            raise
        except Exception:
            raise _fail(VerificationFailure.NOT_STAGED) from None
        claim = _check_claim(raw_claim, workspace_id, source_id, version_id, self._max_bytes)

        try:
            content = await self._reader.read_exact(
                claim.object_key, access_token=access_token
            )
        except SnapshotVerificationError:
            raise
        except Exception:
            raise _fail(VerificationFailure.STORAGE_UNAVAILABLE) from None
        if not isinstance(content, bytes):
            raise _fail(VerificationFailure.STORAGE_UNAVAILABLE)

        # Size is checked before hashing; digest is recomputed from the bytes
        # actually read, never taken from the claim or the reader.
        if len(content) != claim.claimed_size_bytes or len(content) > self._max_bytes:
            raise _fail(VerificationFailure.SIZE_MISMATCH)
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, claim.claimed_sha256):
            raise _fail(VerificationFailure.DIGEST_MISMATCH)

        try:
            await self._gateway.publish(
                access_token, claim, sha256=digest, size_bytes=len(content)
            )
        except SnapshotVerificationError:
            raise
        except Exception:
            raise _fail(VerificationFailure.PUBLISH_REJECTED) from None
        return VerifiedPublication(workspace_id, source_id, version_id, digest, len(content))


class LocalSupabaseVerificationGateway:
    """PostgREST RPC client for the two verifier functions (loopback only).

    Uses the verifier user's token and a public API key; refuses service_role
    tokens. Owned clients ignore proxy/TLS environment variables
    (``trust_env=False``); an injected client is used as given and its setup is
    the caller's responsibility. Not exercised against a live service yet.
    """

    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        timeout_seconds: float = 5.0,
        total_timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or not _is_loopback_host(parsed.hostname)
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not _is_public_api_key(publishable_key)
            or not 0 < timeout_seconds <= 30
            or not _valid_seconds(total_timeout_seconds)
        ):
            raise ValueError("local verifier gateway configuration is invalid")
        self._base_url = base_url.rstrip("/")
        self._key = publishable_key
        self._timeout = timeout_seconds
        self._total = float(total_timeout_seconds)
        self._client = client

    async def begin(
        self, access_token: str, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> StagedSnapshotClaim:
        status, payload = await self._rpc(
            "begin_snapshot_verification",
            access_token,
            {
                "p_workspace_id": str(workspace_id),
                "p_source_id": str(source_id),
                "p_version_id": str(version_id),
            },
        )
        if status in {401, 403}:
            raise _fail(VerificationFailure.NOT_AUTHORISED)
        if status < 200 or status >= 300:
            raise _fail(VerificationFailure.NOT_STAGED)
        try:
            rows = json.loads(payload)
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError
            row = rows[0]
            return StagedSnapshotClaim(
                workspace_id=workspace_id,
                source_id=source_id,
                version_id=version_id,
                object_key=row["out_object_key"],
                claimed_sha256=row["out_claimed_sha256"],
                claimed_size_bytes=row["out_claimed_size_bytes"],
                storage_object_id=UUID(row["out_storage_object_id"]),
                storage_object_version=row["out_storage_object_version"],
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _fail(VerificationFailure.CLAIM_INVALID) from None

    async def publish(
        self, access_token: str, claim: StagedSnapshotClaim, *, sha256: str, size_bytes: int
    ) -> None:
        status, _ = await self._rpc(
            "publish_verified_snapshot",
            access_token,
            {
                "p_workspace_id": str(claim.workspace_id),
                "p_source_id": str(claim.source_id),
                "p_version_id": str(claim.version_id),
                "p_sha256": sha256,
                "p_size_bytes": size_bytes,
                "p_storage_object_id": str(claim.storage_object_id),
                "p_storage_object_version": claim.storage_object_version,
            },
        )
        if status in {401, 403}:
            raise _fail(VerificationFailure.NOT_AUTHORISED)
        if status < 200 or status >= 300:
            raise _fail(VerificationFailure.PUBLISH_REJECTED)

    async def _rpc(self, name: str, access_token: str, body: dict) -> tuple[int, bytes]:
        if not _valid_token(access_token):
            raise _fail(VerificationFailure.INVALID_REQUEST)
        owned = self._client is None
        client = (
            httpx.AsyncClient(timeout=self._timeout, follow_redirects=False, trust_env=False)
            if owned
            else self._client
        )
        headers = {
            "apikey": self._key,
            "authorization": "Bearer " + access_token,
            "content-type": "application/json",
            "accept": "application/json",
            "accept-encoding": "identity",
        }
        received = bytearray()
        try:
            async with asyncio.timeout(self._total):
                async with client.stream(
                    "POST",
                    f"{self._base_url}/rest/v1/rpc/{name}",
                    headers=headers,
                    content=json.dumps(body).encode("utf-8"),
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() not in {"", "identity"}:
                        raise _fail(VerificationFailure.CLAIM_INVALID)
                    async for chunk in response.aiter_bytes():
                        received.extend(chunk)
                        if len(received) > _MAX_RPC_RESPONSE_BYTES:
                            raise _fail(VerificationFailure.CLAIM_INVALID)
                    return response.status_code, bytes(received)
        except SnapshotVerificationError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise _fail(VerificationFailure.STORAGE_UNAVAILABLE) from None
        finally:
            if owned:
                await client.aclose()


class SupabaseStorageSnapshotReader:
    """Read one staged private-bucket object through the local Storage API.

    Requests are restricted to loopback and never follow redirects. Response
    bytes are streamed with a hard limit; a redirect, compressed body, invalid
    length, or non-success status fails closed. This client does not interpret
    Storage ``id`` or ``version`` fields; those remain opaque values bound by
    the database RPC until their behavior is proven against an isolated store.
    """

    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        timeout_seconds: float = 5.0,
        total_timeout_seconds: float = DEFAULT_MAX_VERIFY_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or not _is_loopback_host(parsed.hostname)
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not _is_public_api_key(publishable_key)
            or type(max_snapshot_bytes) is not int
            or max_snapshot_bytes <= 0
            or not 0 < timeout_seconds <= 30
            or not _valid_seconds(total_timeout_seconds)
        ):
            raise ValueError("local snapshot storage configuration is invalid")
        self._base_url = base_url.rstrip("/")
        self._key = publishable_key
        self._max_bytes = max_snapshot_bytes
        self._timeout = timeout_seconds
        self._total = float(total_timeout_seconds)
        self._client = client

    async def read_exact(self, object_key: str, *, access_token: str) -> bytes:
        if not _valid_token(access_token):
            raise _fail(VerificationFailure.INVALID_REQUEST)
        if not self._valid_object_key(object_key):
            raise _fail(VerificationFailure.INVALID_REQUEST)

        owned = self._client is None
        client = (
            httpx.AsyncClient(timeout=self._timeout, follow_redirects=False, trust_env=False)
            if owned
            else self._client
        )
        headers = {
            "apikey": self._key,
            "authorization": "Bearer " + access_token,
            "accept": "application/octet-stream",
            "accept-encoding": "identity",
        }
        encoded_key = quote(object_key, safe="/")
        received = bytearray()
        try:
            async with asyncio.timeout(self._total):
                async with client.stream(
                    "GET",
                    f"{self._base_url}/storage/v1/object/source-snapshots/{encoded_key}",
                    headers=headers,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {401, 403}:
                        raise _fail(VerificationFailure.NOT_AUTHORISED)
                    if response.status_code != 200:
                        raise _fail(VerificationFailure.STORAGE_UNAVAILABLE)
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() not in {"", "identity"}:
                        raise _fail(VerificationFailure.STORAGE_UNAVAILABLE)
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        if not content_length.isascii() or not content_length.isdecimal():
                            raise _fail(VerificationFailure.STORAGE_UNAVAILABLE)
                        if int(content_length) > self._max_bytes:
                            raise _fail(VerificationFailure.SIZE_MISMATCH)
                    # Compression is rejected above, so decoded streaming bytes
                    # are the object bytes and remain bounded by the same cap.
                    async for chunk in response.aiter_bytes():
                        if len(received) + len(chunk) > self._max_bytes:
                            raise _fail(VerificationFailure.SIZE_MISMATCH)
                        received.extend(chunk)
                    return bytes(received)
        except SnapshotVerificationError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise _fail(VerificationFailure.STORAGE_UNAVAILABLE) from None
        finally:
            if owned:
                await client.aclose()

    @staticmethod
    def _valid_object_key(object_key: object) -> bool:
        if not isinstance(object_key, str):
            return False
        parts = object_key.split("/")
        if len(parts) != 6 or parts[0] != "workspace" or parts[2] != "source" or parts[4] != "version":
            return False
        try:
            workspace_id, source_id, version_id = (UUID(parts[i]) for i in (1, 3, 5))
        except (ValueError, TypeError, AttributeError):
            return False
        return object_key == snapshot_object_key(workspace_id, source_id, version_id)
