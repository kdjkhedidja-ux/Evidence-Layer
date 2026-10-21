"""Async, request-bound source authorization that remains deny-all.

This phase verifies the authorization prerequisites at each read attempt but
does not have a source catalog, ACL, or content opener. Even a fully authorized
request is denied before content access. The existing synchronous local/MCP
boundary is deliberately not adapted or bridged here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from uuid import UUID

from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadIntent,
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


def _is_aware(value: datetime) -> bool:
    try:
        return value.tzinfo is not None and value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False


class AsyncTrustedSourceReadBoundary:
    """Revalidate a server-authenticated request before denying source access.

    The bearer token is retained only inside this request-scoped object and is
    omitted from its representation. Neither a client context nor client
    identity is accepted by the constructor. There is intentionally no storage
    dependency or source-opening method in this phase.
    """

    __slots__ = (
        "_verifier",
        "_directory",
        "_authenticated_request",
        "_workspace_id",
        "_clock",
    )

    def __init__(
        self,
        verifier: VerifiedTokenVerifier,
        directory: WorkspaceDirectory,
        *,
        authenticated_request: AuthenticatedRequest,
        workspace_id: UUID,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(verifier, VerifiedTokenVerifier):
            raise TypeError("a verified token verifier is required")
        if type(authenticated_request) is not AuthenticatedRequest:
            raise TypeError("a server-authenticated request is required")
        if (
            type(authenticated_request.principal) is not Principal
            or not isinstance(authenticated_request.access_token, str)
            or not authenticated_request.access_token.strip()
        ):
            raise TypeError("server-authenticated identity is malformed")
        if type(workspace_id) is not UUID:
            raise TypeError("a server-selected workspace is required")

        self._verifier = verifier
        self._directory = directory
        self._authenticated_request = authenticated_request
        self._workspace_id = workspace_id
        self._clock = clock

    async def open_sources(
        self,
        context: SourceRequestContext,
        intents: Sequence[SourceReadIntent],
    ) -> SourceReadDenial:
        """Recheck identity and membership, then fail closed before any open."""
        if type(context) is not SourceRequestContext:
            return SourceReadDenial(SourceReadDenialReason.MISSING_CONTEXT)

        try:
            verified = await self._verifier.verify_access_token(
                self._authenticated_request.access_token
            )
        except AuthenticationRejected:
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)
        except AuthenticationUnavailable:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        if (
            type(verified) is not VerifiedAccessToken
            or type(verified.principal) is not Principal
            or not isinstance(verified.principal.issuer, str)
            or not verified.principal.issuer
            or type(verified.principal.subject) is not UUID
            or type(verified.expires_at) is not datetime
            or not _is_aware(verified.expires_at)
            or verified.principal != self._authenticated_request.principal
        ):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)

        if not all(
            isinstance(value, str) and value
            for value in (
                context.request_id,
                context.operation_id,
                context.principal_id,
                context.tenant_id,
                context.policy_version,
            )
        ) or not isinstance(context.expires_at, datetime) or not _is_aware(
            context.expires_at
        ):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)

        if (
            context.principal_id != str(verified.principal.subject)
            or context.tenant_id != str(self._workspace_id)
            or context.policy_version != SOURCE_POLICY_VERSION
        ):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)

        try:
            now = self._clock()
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        if not isinstance(now, datetime) or not _is_aware(now):
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        now = now.astimezone(timezone.utc)
        token_expiry = verified.expires_at.astimezone(timezone.utc)
        context_expiry = context.expires_at.astimezone(timezone.utc)
        if token_expiry <= now or context_expiry <= now or context_expiry > token_expiry:
            return SourceReadDenial(SourceReadDenialReason.EXPIRED)

        try:
            await resolve_workspace_context(
                self._directory,
                verified.principal,
                self._authenticated_request.access_token,
                self._workspace_id,
            )
        except WorkspaceAccessDenied:
            return SourceReadDenial(SourceReadDenialReason.REVOKED)
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        try:
            now_after_membership = self._clock()
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        if not isinstance(now_after_membership, datetime) or not _is_aware(
            now_after_membership
        ):
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        now_after_membership = now_after_membership.astimezone(timezone.utc)
        if token_expiry <= now_after_membership or context_expiry <= now_after_membership:
            return SourceReadDenial(SourceReadDenialReason.EXPIRED)

        if not isinstance(intents, Sequence) or isinstance(intents, (str, bytes)):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)
        if not intents:
            return SourceReadDenial(SourceReadDenialReason.UNKNOWN_SOURCE)
        for intent in intents:
            if (
                type(intent) is not SourceReadIntent
                or not isinstance(intent.source_id, str)
                or not intent.source_id
                or intent.operation not in {"retrieve", "index", "refresh"}
                or (
                    intent.expected_version is not None
                    and not isinstance(intent.expected_version, str)
                )
            ):
                return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)

        # Phase 2 proves read-time authorization only. No content-opening path
        # exists until an approved catalog, ACL, and storage implementation do.
        return SourceReadDenial(SourceReadDenialReason.DENY_ALL)
