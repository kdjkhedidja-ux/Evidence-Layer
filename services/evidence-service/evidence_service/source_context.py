"""Server-internal issuance of short-lived source request contexts.

The context values are derived from a verified bearer token and an active
workspace membership lookup. The dataclass itself is not proof of provenance;
callers must not deserialize or accept one from a client.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from evidence_layer.source_access import SourceRequestContext

from evidence_service.auth import (
    AuthenticationRejected,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
    VerifiedTokenVerifier,
)
from evidence_service.tenant_context import (
    WorkspaceAccessDenied,
    WorkspaceContext,
    WorkspaceDirectory,
    resolve_workspace_context,
)

DEFAULT_SOURCE_CONTEXT_TTL = timedelta(minutes=1)
MAX_SOURCE_CONTEXT_TTL = timedelta(minutes=5)
SOURCE_POLICY_VERSION = "source-policy-v1"


class SourceContextDenied(Exception):
    """Identity or membership did not authorize source-context issuance."""


class SourceContextUnavailable(Exception):
    """A trusted identity or membership dependency could not be checked."""


def _is_aware(value: datetime) -> bool:
    try:
        return value.tzinfo is not None and value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False


class TrustedSourceContextIssuer:
    """Issue contexts from server-owned verifier and workspace directory.

    Construct this object only in trusted server composition. ``issue`` accepts
    the raw bearer token and selected workspace, never caller-provided identity,
    tenant, role, policy, request IDs, or expiry claims.
    """

    def __init__(
        self,
        verifier: VerifiedTokenVerifier,
        directory: WorkspaceDirectory,
        *,
        ttl: timedelta = DEFAULT_SOURCE_CONTEXT_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(ttl) is not timedelta or ttl <= timedelta(0):
            raise ValueError("source context TTL must be a positive timedelta")
        if ttl > MAX_SOURCE_CONTEXT_TTL:
            raise ValueError("source context TTL cannot exceed five minutes")
        self._verifier = verifier
        self._directory = directory
        self._ttl = ttl
        self._clock = clock
        self._id_factory = id_factory

    async def issue(self, access_token: str, workspace_id: UUID) -> SourceRequestContext:
        if not isinstance(access_token, str) or not access_token.strip():
            raise SourceContextDenied("A valid bearer token is required")
        if type(workspace_id) is not UUID:
            raise SourceContextDenied("A valid workspace is required")

        try:
            verified = await self._verifier.verify_access_token(access_token)
        except AuthenticationRejected as exc:
            raise SourceContextDenied("Identity was not verified") from exc
        except AuthenticationUnavailable as exc:
            raise SourceContextUnavailable("Identity verification is unavailable") from exc
        except Exception as exc:
            raise SourceContextUnavailable("Identity verification failed") from exc

        if type(verified) is not VerifiedAccessToken:
            raise SourceContextDenied("Verified identity claims are malformed")

        principal = verified.principal
        if (
            type(principal) is not Principal
            or not isinstance(getattr(principal, "issuer", None), str)
            or not principal.issuer
            or type(getattr(principal, "subject", None)) is not UUID
            or type(verified.expires_at) is not datetime
            or not _is_aware(verified.expires_at)
        ):
            raise SourceContextDenied("Verified identity claims are malformed")

        try:
            now = self._clock()
        except Exception as exc:
            raise SourceContextUnavailable("The server clock is unavailable") from exc
        if not isinstance(now, datetime) or not _is_aware(now):
            raise SourceContextUnavailable("The server clock is invalid")
        now = now.astimezone(timezone.utc)
        token_expiry = verified.expires_at.astimezone(timezone.utc)
        if token_expiry <= now:
            raise SourceContextDenied("The bearer token has expired")

        try:
            context: WorkspaceContext = await resolve_workspace_context(
                self._directory,
                principal,
                access_token,
                workspace_id,
            )
        except WorkspaceAccessDenied as exc:
            raise SourceContextDenied("Active workspace membership is required") from exc
        except Exception as exc:
            raise SourceContextUnavailable("Workspace membership could not be checked") from exc

        if (
            type(context) is not WorkspaceContext
            or context.workspace_id != workspace_id
            or context.role not in {"owner", "admin", "member"}
        ):
            raise SourceContextDenied("Workspace membership is malformed")

        try:
            issued_at = self._clock()
        except Exception as exc:
            raise SourceContextUnavailable("The server clock is unavailable") from exc
        if not isinstance(issued_at, datetime) or not _is_aware(issued_at):
            raise SourceContextUnavailable("The server clock is invalid")
        issued_at = issued_at.astimezone(timezone.utc)
        if token_expiry <= issued_at:
            raise SourceContextDenied("The bearer token has insufficient remaining lifetime")
        expires_at = min(issued_at + self._ttl, token_expiry)

        request_id = self._id_factory()
        operation_id = self._id_factory()
        if type(request_id) is not UUID or type(operation_id) is not UUID:
            raise SourceContextUnavailable("Server request identifiers are invalid")

        return SourceRequestContext(
            request_id=str(request_id),
            operation_id=str(operation_id),
            principal_id=str(principal.subject),
            tenant_id=str(context.workspace_id),
            policy_version=SOURCE_POLICY_VERSION,
            expires_at=expires_at,
        )
