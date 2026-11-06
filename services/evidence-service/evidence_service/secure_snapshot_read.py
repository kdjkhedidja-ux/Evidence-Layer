"""Local, fail-closed source snapshot read path.

This module is intentionally not composed by the HTTP app, MCP server, or
production retriever. Its dependencies are injected so the authorization and
integrity order can be exercised locally while the shipped read path remains
DENY_ALL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx

from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadError,
    SourceRequestContext,
)
from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationRejected,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
    VerifiedTokenVerifier,
)
from evidence_service.source_context import SOURCE_POLICY_VERSION
from evidence_service.tenant_context import (
    WorkspaceAccessDenied,
    WorkspaceDirectory,
    resolve_workspace_context,
)

DEFAULT_READ_CONTEXT_TTL = timedelta(seconds=30)
MAX_READ_CONTEXT_TTL = timedelta(minutes=1)
DEFAULT_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_ABSOLUTE_MAX_READ_BYTES = 64 * 1024 * 1024
# Overall wall-clock bounds. They cap the whole operation, not each network
# step, so a server that drips bytes or a dependency that never answers cannot
# hold a read open past the limit (or past the read context lifetime).
DEFAULT_MAX_READ_SECONDS = 10.0
DEFAULT_STORAGE_TOTAL_TIMEOUT_SECONDS = 10.0
_ABSOLUTE_MAX_READ_SECONDS = 60.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_seconds(value: object) -> bool:
    return (
        type(value) in (int, float)
        and 0 < value <= _ABSOLUTE_MAX_READ_SECONDS  # type: ignore[operator]
    )


class SnapshotState(str, Enum):
    STAGED = "staged"
    PUBLISHED = "published"


@dataclass(frozen=True)
class SourceSnapshotManifest:
    """Catalog record binding a logical snapshot version to one object key."""

    workspace_id: UUID
    source_id: UUID
    owner_id: UUID
    version_id: UUID
    object_key: str
    sha256: str
    size_bytes: int
    state: SnapshotState
    created_at: datetime


@dataclass(frozen=True)
class VerifiedSnapshot:
    """Complete content returned only after full size and digest validation."""

    manifest: SourceSnapshotManifest
    content: bytes


class SnapshotCatalog(Protocol):
    async def resolve_version(
        self, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> SourceSnapshotManifest | None: ...

    async def publish_if_absent(self, manifest: SourceSnapshotManifest) -> None: ...


class SnapshotSourceAccessPolicy(Protocol):
    async def may_read(
        self,
        principal_id: UUID,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> bool: ...


class SnapshotObjectStore(Protocol):
    async def read_exact(self, object_key: str, *, access_token: str) -> bytes: ...

    async def put_if_absent(
        self, object_key: str, content: bytes, *, access_token: str
    ) -> None: ...


class SnapshotObjectUnavailable(Exception):
    """Object API failure without exposing a URL, token, or response body."""


class SnapshotObjectConflict(Exception):
    """The immutable object key already exists."""


class InMemorySnapshotCatalog:
    """Thread-safe local catalog for focused contract tests only."""

    def __init__(self) -> None:
        from threading import Lock

        self._lock = Lock()
        self._entries: dict[tuple[UUID, UUID, UUID], SourceSnapshotManifest] = {}

    async def resolve_version(self, workspace_id, source_id, version_id):
        await asyncio.sleep(0)
        with self._lock:
            return self._entries.get((workspace_id, source_id, version_id))

    async def publish_if_absent(self, manifest: SourceSnapshotManifest) -> None:
        key = (manifest.workspace_id, manifest.source_id, manifest.version_id)
        await asyncio.sleep(0)
        with self._lock:
            if key in self._entries:
                raise SnapshotObjectConflict
            self._entries[key] = manifest


class InMemoryImmutableObjectStore:
    """Atomic write-once object store for unit tests, not Storage evidence."""

    def __init__(self) -> None:
        from threading import Lock

        self._lock = Lock()
        self._objects: dict[str, bytes] = {}
        self.opened: list[str] = []
        self.read_override: Callable[[str, bytes], bytes] | None = None

    async def read_exact(self, object_key: str, *, access_token: str) -> bytes:
        del access_token
        await asyncio.sleep(0)
        with self._lock:
            self.opened.append(object_key)
            try:
                value = self._objects[object_key]
            except KeyError as exc:
                raise SnapshotObjectUnavailable from exc
        return self.read_override(object_key, value) if self.read_override else value

    async def put_if_absent(
        self, object_key: str, content: bytes, *, access_token: str
    ) -> None:
        del access_token
        await asyncio.sleep(0)
        with self._lock:
            if object_key in self._objects:
                raise SnapshotObjectConflict
            self._objects[object_key] = bytes(content)


class LocalSupabaseStorageObjectStore:
    """HTTP adapter restricted to a loopback Supabase Storage endpoint.

    It uses the request user's bearer token and a public API key. It never
    accepts service-role credentials and is not suitable for hosted URLs.

    Every operation is bounded by ``total_timeout_seconds`` in addition to the
    per-step ``timeout_seconds``, so the adapter is safe on its own and does not
    rely on a caller wrapping it. The client the adapter creates ignores
    proxy/TLS environment variables (``trust_env=False``). An injected
    ``client`` is used as given: its transport, proxy, redirect and TLS settings
    are the caller's responsibility and are not validated here.
    """

    def __init__(
        self,
        *,
        base_url: str,
        bucket: str,
        publishable_key: str,
        timeout_seconds: float = 5.0,
        total_timeout_seconds: float = DEFAULT_STORAGE_TOTAL_TIMEOUT_SECONDS,
        max_read_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        host = parsed.hostname
        is_loopback = host == "localhost"
        if host and not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if (
            parsed.scheme != "http"
            or not is_loopback
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not bucket
            or "/" in bucket
            or not _is_public_api_key(publishable_key)
            or not 0 < timeout_seconds <= 30
            or not _valid_seconds(total_timeout_seconds)
            or type(max_read_bytes) is not int
            or not 0 < max_read_bytes <= _ABSOLUTE_MAX_READ_BYTES
        ):
            raise ValueError("local Storage configuration is invalid")
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket
        self._publishable_key = publishable_key
        self._timeout = timeout_seconds
        self._total_timeout = float(total_timeout_seconds)
        self._max_read_bytes = max_read_bytes
        self._client = client

    async def read_exact(self, object_key: str, *, access_token: str) -> bytes:
        """Stream the object with a hard cap on bytes.

        Compression is refused outright (``Accept-Encoding: identity`` and any
        other ``Content-Encoding`` is treated as unavailable), so a small
        compressed chunk can never expand past the cap before it is checked.
        The body is never buffered beyond ``max_read_bytes`` plus one
        transport chunk.
        """
        url, headers = self._prepare(
            object_key, access_token, {"accept-encoding": "identity"}
        )
        owned = self._client is None
        client = (
            httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, trust_env=False
            )
            if owned
            else self._client
        )
        body = bytearray()
        try:
            async with asyncio.timeout(self._total_timeout):
                async with client.stream(
                    "GET",
                    url,
                    headers=headers,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise SnapshotObjectUnavailable
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() not in {"", "identity"}:
                        raise SnapshotObjectUnavailable
                    declared = response.headers.get("content-length")
                    # ASCII decimal digits only; the body is still counted
                    # while streaming, so this header is a cheap early reject.
                    if declared is not None and (
                        not (declared.isascii() and declared.isdigit())
                        or int(declared) > self._max_read_bytes
                    ):
                        raise SnapshotObjectUnavailable
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_read_bytes:
                            raise SnapshotObjectUnavailable
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            raise SnapshotObjectUnavailable from exc
        finally:
            if owned:
                await client.aclose()
        return bytes(body)

    async def put_if_absent(
        self, object_key: str, content: bytes, *, access_token: str
    ) -> None:
        response = await self._request(
            "POST",
            object_key,
            access_token=access_token,
            content=content,
            extra_headers={
                "content-type": "application/octet-stream",
                "x-upsert": "false",
            },
        )
        if response.status_code in {409, 412}:
            raise SnapshotObjectConflict
        if response.status_code < 200 or response.status_code >= 300:
            raise SnapshotObjectUnavailable

    def _prepare(
        self,
        object_key: str,
        access_token: str,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        if not isinstance(access_token, str) or not access_token.strip():
            raise SnapshotObjectUnavailable
        key = _validate_object_key(object_key)
        path = "/".join(quote(part, safe="-_.~") for part in key.split("/"))
        url = f"{self._base_url}/storage/v1/object/{quote(self._bucket, safe='-_.~')}/{path}"
        headers = {
            "apikey": self._publishable_key,
            "authorization": f"Bearer {access_token}",
        }
        if extra_headers:
            headers.update(extra_headers)
        return url, headers

    async def _request(
        self,
        method: str,
        object_key: str,
        *,
        access_token: str,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url, headers = self._prepare(object_key, access_token, extra_headers)
        try:
            async with asyncio.timeout(self._total_timeout):
                if self._client is not None:
                    response = await self._client.request(
                        method,
                        url,
                        headers=headers,
                        content=content,
                        timeout=self._timeout,
                        follow_redirects=False,
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=self._timeout,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        response = await client.request(
                            method,
                            url,
                            headers=headers,
                            content=content,
                        )
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            raise SnapshotObjectUnavailable from exc
        return response


class SecureSnapshotReadPath:
    """Authenticate, recheck membership and source ACL, then deliver verified bytes."""

    def __init__(
        self,
        *,
        verifier: VerifiedTokenVerifier,
        workspace_directory: WorkspaceDirectory,
        catalog: SnapshotCatalog,
        acl: SnapshotSourceAccessPolicy,
        store: SnapshotObjectStore,
        account_lifecycle_check: Callable[[AuthenticatedRequest], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        context_ttl: timedelta = DEFAULT_READ_CONTEXT_TTL,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        max_read_seconds: float = DEFAULT_MAX_READ_SECONDS,
    ) -> None:
        if type(context_ttl) is not timedelta or not timedelta(0) < context_ttl <= MAX_READ_CONTEXT_TTL:
            raise ValueError("read context TTL is outside the supported range")
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
            raise ValueError("snapshot byte limit must be positive")
        if not _valid_seconds(max_read_seconds):
            raise ValueError("read deadline is outside the supported range")
        self._verifier = verifier
        self._directory = workspace_directory
        self._catalog = catalog
        self._acl = acl
        self._store = store
        self._account_lifecycle_check = account_lifecycle_check
        self._clock = clock
        self._context_ttl = context_ttl
        self._max_bytes = max_snapshot_bytes
        self._max_read_seconds = float(max_read_seconds)

    async def read(
        self,
        authenticated_request: AuthenticatedRequest,
        *,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> VerifiedSnapshot:
        if (
            type(authenticated_request) is not AuthenticatedRequest
            or type(authenticated_request.principal) is not Principal
            or type(workspace_id) is not UUID
            or type(source_id) is not UUID
            or type(version_id) is not UUID
        ):
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)

        # One wall-clock budget covers every awaited dependency (identity,
        # membership, catalog, ACL, Storage). It starts at ``max_read_seconds``
        # and is lowered to the remaining read-context lifetime as soon as the
        # context exists. Expiry cancels the pending dependency (which closes
        # its resources) and fails closed without delivering any content.
        try:
            async with asyncio.timeout(self._max_read_seconds) as budget:
                return await self._read_within_budget(
                    authenticated_request,
                    workspace_id,
                    source_id,
                    version_id,
                    budget,
                )
        except TimeoutError as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc

    def _limit_budget_to_context(
        self, budget: asyncio.Timeout, context: SourceRequestContext
    ) -> None:
        remaining = (
            context.expires_at.astimezone(timezone.utc) - self._now()
        ).total_seconds()
        deadline = asyncio.get_running_loop().time() + max(remaining, 0.0)
        current = budget.when()
        budget.reschedule(deadline if current is None else min(current, deadline))

    async def _read_within_budget(
        self,
        authenticated_request: AuthenticatedRequest,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
        budget: asyncio.Timeout,
    ) -> VerifiedSnapshot:
        await self._check_account_lifecycle(authenticated_request)
        principal, context = await self._trusted_membership(
            authenticated_request, workspace_id, expected_expiry=None
        )
        self._limit_budget_to_context(budget, context)
        await self._check_account_lifecycle(authenticated_request)
        manifest = await self._resolve_manifest(workspace_id, source_id, version_id)
        self._validate_manifest(manifest, workspace_id, source_id, version_id)

        await self._check_account_lifecycle(authenticated_request)
        await self._require_acl(authenticated_request, principal.subject, manifest)
        await self._revalidate_before_open(
            authenticated_request,
            workspace_id,
            principal,
            context,
            manifest,
        )

        await self._check_account_lifecycle(authenticated_request)
        try:
            content = await self._store.read_exact(
                manifest.object_key,
                access_token=authenticated_request.access_token,
            )
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if not isinstance(content, bytes) or len(content) != manifest.size_bytes:
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, manifest.sha256):
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)

        # Withhold the complete buffered payload if token, membership, context,
        # or source ACL changed while Storage returned the bytes.
        await self._revalidate_before_delivery(
            authenticated_request,
            workspace_id,
            principal,
            context,
            manifest,
        )
        await self._check_account_lifecycle(authenticated_request)
        return VerifiedSnapshot(manifest=manifest, content=content)

    async def _check_account_lifecycle(
        self, authenticated_request: AuthenticatedRequest
    ) -> None:
        if self._account_lifecycle_check is not None:
            await self._account_lifecycle_check(authenticated_request)

    async def _trusted_membership(
        self,
        authenticated_request: AuthenticatedRequest,
        workspace_id: UUID,
        *,
        expected_expiry: datetime | None,
        context: SourceRequestContext | None = None,
    ) -> tuple[Principal, SourceRequestContext]:
        await self._check_account_lifecycle(authenticated_request)
        try:
            verified = await self._verifier.verify_access_token(
                authenticated_request.access_token
            )
        except AuthenticationRejected as exc:
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH) from exc
        except AuthenticationUnavailable as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if (
            type(verified) is not VerifiedAccessToken
            or type(verified.principal) is not Principal
            or verified.principal != authenticated_request.principal
            or not _aware(verified.expires_at)
        ):
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)
        now = self._now()
        if verified.expires_at.astimezone(timezone.utc) <= now:
            raise _denied(SourceReadDenialReason.EXPIRED)
        if expected_expiry is not None and expected_expiry <= now:
            raise _denied(SourceReadDenialReason.EXPIRED)
        if (
            expected_expiry is not None
            and expected_expiry > verified.expires_at.astimezone(timezone.utc)
        ):
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)
        if context is not None and (
            type(context) is not SourceRequestContext
            or context.principal_id != str(verified.principal.subject)
            or context.tenant_id != str(workspace_id)
            or context.policy_version != SOURCE_POLICY_VERSION
            or not context.request_id
            or not context.operation_id
            or not _aware(context.expires_at)
            or context.expires_at.astimezone(timezone.utc)
            > verified.expires_at.astimezone(timezone.utc)
        ):
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)
        if context is not None and context.expires_at.astimezone(timezone.utc) <= now:
            raise _denied(SourceReadDenialReason.EXPIRED)
        try:
            membership = await resolve_workspace_context(
                self._directory,
                verified.principal,
                authenticated_request.access_token,
                workspace_id,
            )
        except WorkspaceAccessDenied as exc:
            raise _denied(SourceReadDenialReason.REVOKED) from exc
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        now_after_membership = self._now()
        if verified.expires_at.astimezone(timezone.utc) <= now_after_membership:
            raise _denied(SourceReadDenialReason.EXPIRED)
        if expected_expiry is not None and expected_expiry <= now_after_membership:
            raise _denied(SourceReadDenialReason.EXPIRED)
        if context is not None:
            if context.expires_at.astimezone(timezone.utc) <= now_after_membership:
                raise _denied(SourceReadDenialReason.EXPIRED)
            trusted_context = context
        else:
            context_expiry = min(
                now_after_membership + self._context_ttl,
                verified.expires_at.astimezone(timezone.utc),
            )
            if context_expiry <= now_after_membership:
                raise _denied(SourceReadDenialReason.EXPIRED)
            trusted_context = SourceRequestContext(
                request_id=str(uuid4()),
                operation_id=str(uuid4()),
                principal_id=str(verified.principal.subject),
                tenant_id=str(workspace_id),
                policy_version=SOURCE_POLICY_VERSION,
                expires_at=context_expiry,
            )
        if membership.workspace_id != workspace_id:
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)
        return verified.principal, trusted_context

    async def _resolve_manifest(
        self, workspace_id: UUID, source_id: UUID, version_id: UUID
    ) -> SourceSnapshotManifest:
        # Keep the lifecycle check outside the catalog error translation so a
        # denial is returned as the generic account gate response.
        try:
            manifest = await self._catalog.resolve_version(
                workspace_id, source_id, version_id
            )
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if manifest is None:
            raise _denied(SourceReadDenialReason.VERSION_MISMATCH)
        if type(manifest) is not SourceSnapshotManifest:
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)
        return manifest

    def _validate_manifest(
        self,
        manifest: SourceSnapshotManifest,
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> None:
        if (
            manifest.state is not SnapshotState.PUBLISHED
            or manifest.workspace_id != workspace_id
            or manifest.source_id != source_id
            or manifest.version_id != version_id
            or manifest.object_key
            != snapshot_object_key(workspace_id, source_id, version_id)
        ):
            raise _denied(SourceReadDenialReason.VERSION_MISMATCH)
        if (
            type(manifest.owner_id) is not UUID
            or not isinstance(manifest.object_key, str)
            or not manifest.object_key
            or not _SHA256_RE.fullmatch(manifest.sha256)
            or type(manifest.size_bytes) is not int
            or not 0 <= manifest.size_bytes <= self._max_bytes
            or not _aware(manifest.created_at)
        ):
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)
        try:
            _validate_object_key(manifest.object_key)
        except ValueError as exc:
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA) from exc

    async def _require_acl(
        self,
        authenticated_request: AuthenticatedRequest,
        principal_id: UUID,
        manifest: SourceSnapshotManifest,
    ) -> None:
        await self._check_account_lifecycle(authenticated_request)
        try:
            allowed = await self._acl.may_read(
                principal_id,
                manifest.workspace_id,
                manifest.source_id,
                manifest.version_id,
            )
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if allowed is not True:
            raise _denied(SourceReadDenialReason.ACCESS_DENIED)

    async def _revalidate_before_open(
        self,
        authenticated_request: AuthenticatedRequest,
        workspace_id: UUID,
        principal: Principal,
        context: SourceRequestContext,
        manifest: SourceSnapshotManifest,
    ) -> None:
        checked_principal, _ = await self._trusted_membership(
            authenticated_request,
            workspace_id,
            expected_expiry=context.expires_at.astimezone(timezone.utc),
            context=context,
        )
        if checked_principal != principal:
            raise _denied(SourceReadDenialReason.SCOPE_MISMATCH)
        await self._require_acl(authenticated_request, principal.subject, manifest)

    async def _revalidate_before_delivery(
        self,
        authenticated_request: AuthenticatedRequest,
        workspace_id: UUID,
        principal: Principal,
        context: SourceRequestContext,
        manifest: SourceSnapshotManifest,
    ) -> None:
        await self._revalidate_before_open(
            authenticated_request,
            workspace_id,
            principal,
            context,
            manifest,
        )

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if not _aware(value):
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE)
        return value.astimezone(timezone.utc)


class SnapshotPublisher:
    """Local/test publisher: upload with a create-only write, then read back.

    It reads the object back with the uploader's own token, which the private
    Storage bucket policies deny before publication, and it records the
    catalog entry itself. It is therefore NOT the trusted verification path:
    production publication needs a separate verifier that recomputes the
    SHA-256 from the stored bytes. Not composed by the HTTP app or MCP.
    """

    def __init__(
        self,
        *,
        catalog: SnapshotCatalog,
        store: SnapshotObjectStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._clock = clock
        self._max_bytes = max_snapshot_bytes

    async def publish(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        owner_id: UUID,
        version_id: UUID,
        content: bytes,
        access_token: str,
    ) -> SourceSnapshotManifest:
        if (
            any(type(value) is not UUID for value in (workspace_id, source_id, owner_id, version_id))
            or not isinstance(content, bytes)
            or len(content) > self._max_bytes
            or not isinstance(access_token, str)
            or not access_token.strip()
        ):
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)
        object_key = snapshot_object_key(workspace_id, source_id, version_id)
        digest = hashlib.sha256(content).hexdigest()
        try:
            await self._store.put_if_absent(
                object_key, content, access_token=access_token
            )
            stored = await self._store.read_exact(
                object_key, access_token=access_token
            )
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if (
            not isinstance(stored, bytes)
            or len(stored) != len(content)
            or not hmac.compare_digest(hashlib.sha256(stored).hexdigest(), digest)
        ):
            raise _denied(SourceReadDenialReason.INVALID_SOURCE_DATA)
        try:
            created_at = self._clock()
        except Exception as exc:
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE) from exc
        if not _aware(created_at):
            raise _denied(SourceReadDenialReason.STORAGE_FAILURE)
        manifest = SourceSnapshotManifest(
            workspace_id=workspace_id,
            source_id=source_id,
            owner_id=owner_id,
            version_id=version_id,
            object_key=object_key,
            sha256=digest,
            size_bytes=len(content),
            state=SnapshotState.PUBLISHED,
            created_at=created_at.astimezone(timezone.utc),
        )
        try:
            await self._catalog.publish_if_absent(manifest)
        except Exception as exc:
            # Object remains an unpublished orphan; readers resolve only the
            # catalog, so no manifest points at a failed/concurrent write.
            raise _denied(SourceReadDenialReason.VERSION_MISMATCH) from exc
        return manifest


def snapshot_object_key(workspace_id: UUID, source_id: UUID, version_id: UUID) -> str:
    """Derive a stable, server-owned locator from immutable logical IDs."""
    if any(type(value) is not UUID for value in (workspace_id, source_id, version_id)):
        raise ValueError("snapshot identifiers must be UUIDs")
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


def _is_public_api_key(value: str) -> bool:
    """Accept a publishable key or a legacy anon JWT, never service_role."""
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith("sb_publishable_"):
        return len(value) > len("sb_publishable_")
    parts = value.split(".")
    if len(parts) != 3:
        return False
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(claims, dict) and claims.get("role") == "anon"


def _validate_object_key(object_key: str) -> str:
    if (
        not isinstance(object_key, str)
        or not object_key
        or object_key.startswith("/")
        or "\\" in object_key
        or any(part in {"", ".", ".."} for part in object_key.split("/"))
    ):
        raise ValueError("object key is invalid")
    return object_key


def _aware(value: datetime) -> bool:
    try:
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False


def _denied(reason: SourceReadDenialReason) -> SourceReadError:
    return SourceReadError(SourceReadDenial(reason))
