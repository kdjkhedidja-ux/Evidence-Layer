"""Authorization filtering that runs before any router sees a source.

TRUST ASSUMPTION (not enforced here). ``SourceRequestContext`` is a plain
dataclass: any caller can construct one for any tenant, principal, and
lifetime. This layer treats ``context.tenant_id`` as given and only checks its
shape. Tenant isolation therefore holds only if a trusted host built the
context from verified membership (a verified ``WorkspaceContext`` plus the
authenticated ``Principal``) and the host-owned policy independently checks
that membership. Until a single trusted constructor with a bounded lifetime
exists, no allowing policy may be shipped or wired: ``DenyAllSourcePolicy`` is
the only policy provided, and it denies everything.

A router only ever receives an ``AuthorizedCatalog``; it never sees sources
from other workspaces, sources whose status is not routable, or sources the
policy did not allow. The ``AuthorizedCatalog`` type is not proof of
authorization: anyone can construct one. It documents what the filter produced.

The clock used for expiry is not a parameter of the public API; tests replace
the module-level ``_now`` function.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import islice
from typing import Protocol

from evidence_layer.routing.contract import (
    ROUTABLE_STATUSES,
    SourceDescriptor,
    SourceStatus,
    validate_identifier,
)
from evidence_layer.source_access import SourceRequestContext, context_is_expired

#: Upper bound on candidates read from a store and ids read from a policy, so a
#: huge or endless iterable cannot hang routing.
MAX_CANDIDATES = 10_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceAccessPolicy(Protocol):
    """Host-owned decision about which candidate sources a principal may use."""

    def authorized_source_ids(
        self, context: SourceRequestContext, candidate_ids: frozenset[str]
    ) -> Iterable[str]: ...


class DenyAllSourcePolicy:
    """Safe default while no trusted source host exists."""

    def authorized_source_ids(
        self, context: SourceRequestContext, candidate_ids: frozenset[str]
    ) -> Iterable[str]:
        return ()


class CatalogDenialReason(str, Enum):
    CONTEXT_EXPIRED = "context_expired"
    POLICY_ERROR = "policy_error"
    SOURCE_STORE_ERROR = "source_store_error"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    NO_AUTHORIZED_SOURCES = "no_authorized_sources"


def context_is_well_formed(context: object) -> bool:
    """True when the context carries identifiers safe to log and bind to.

    This checks shape only; it does not establish that a trusted host built the
    context (see the trust assumption in the module docstring). Only an exact
    ``SourceRequestContext`` is accepted, never a subclass.
    """
    if type(context) is not SourceRequestContext:
        return False
    if not isinstance(context.expires_at, datetime):
        return False
    try:
        for name in ("tenant_id", "request_id", "operation_id", "policy_version"):
            validate_identifier(name, getattr(context, name))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class AuthorizedCatalog:
    """Sources one tenant may route over, ordered by ``source_id``.

    Constructing one is open to any caller, so holding an instance is not proof
    of authorization; it only records what ``filter_authorized_catalog`` kept.
    """

    tenant_id: str
    policy_version: str
    sources: tuple[SourceDescriptor, ...] = ()

    def __post_init__(self) -> None:
        validate_identifier("tenant_id", self.tenant_id)
        ids = [source.source_id for source in self.sources]
        if len(set(ids)) != len(ids) or ids != sorted(ids):
            raise ValueError("sources must be unique and ordered by source_id")
        for source in self.sources:
            if source.workspace_id != self.tenant_id:
                raise ValueError("catalog contains a source from another workspace")
            if source.status not in ROUTABLE_STATUSES:
                raise ValueError("catalog contains a source that is not routable")

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(source.source_id for source in self.sources)


@dataclass(frozen=True)
class CatalogFilterResult:
    catalog: AuthorizedCatalog
    denial: CatalogDenialReason | None = None


def filter_authorized_catalog(
    context: SourceRequestContext,
    candidates: Iterable[SourceDescriptor],
    policy: SourceAccessPolicy,
) -> CatalogFilterResult:
    """Return only sources this tenant may route over; fail closed on any doubt.

    Candidates may come from a store that mixes workspaces and is not trusted:
    only exact ``SourceDescriptor`` instances with well-formed identifiers are
    accepted (a subclass or malformed identifier is a denial), at most
    ``MAX_CANDIDATES`` are read, and a store that raises while being read is a
    denial. Sources owned by another workspace, or whose status is not
    routable, are dropped regardless of what the policy says. An identifier
    that appears more than once for the tenant, whatever its status, is
    ambiguous and dropped. Identifiers the policy returns that are not
    candidates are ignored, and the policy's output is bounded the same way.

    A store that silently returns too little (truncated or stale data) cannot
    be detected here; re-verification at the source-read boundary is required
    before any content is opened.

    ``context`` must be well formed (``context_is_well_formed``); the routing
    pipeline checks this and stops safely, while this function raises
    ``ValueError`` on an unusable tenant identifier.
    """
    if context_is_expired(context, now=_now()):
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.CONTEXT_EXPIRED
        )

    collected = _collect(candidates)
    if collected is None:
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.SOURCE_STORE_ERROR
        )
    if len(collected) > MAX_CANDIDATES:
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.CANDIDATE_LIMIT_EXCEEDED
        )

    eligible: dict[str, SourceDescriptor] = {}
    seen: set[str] = set()
    ambiguous: set[str] = set()
    for source in collected:
        if not _is_sound_descriptor(source):
            return CatalogFilterResult(
                _empty(context), CatalogDenialReason.SOURCE_STORE_ERROR
            )
        if source.workspace_id != context.tenant_id:
            continue
        if source.source_id in seen:
            ambiguous.add(source.source_id)
        seen.add(source.source_id)
        if source.status in ROUTABLE_STATUSES:
            eligible[source.source_id] = source
    for source_id in ambiguous:
        eligible.pop(source_id, None)

    try:
        returned = policy.authorized_source_ids(context, frozenset(eligible))
        allowed_ids = list(islice(returned, MAX_CANDIDATES + 1))
    except Exception:
        return CatalogFilterResult(_empty(context), CatalogDenialReason.POLICY_ERROR)
    if len(allowed_ids) > MAX_CANDIDATES:
        return CatalogFilterResult(_empty(context), CatalogDenialReason.POLICY_ERROR)
    allowed = frozenset(item for item in allowed_ids if type(item) is str)

    sources = tuple(eligible[source_id] for source_id in sorted(eligible.keys() & allowed))
    if not sources:
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.NO_AUTHORIZED_SOURCES
        )
    return CatalogFilterResult(
        AuthorizedCatalog(context.tenant_id, context.policy_version, sources)
    )


def _collect(candidates: object) -> list[object] | None:
    """Read at most ``MAX_CANDIDATES + 1`` items; None if the store fails."""
    try:
        return list(islice(candidates, MAX_CANDIDATES + 1))  # type: ignore[call-overload]
    except Exception:
        return None


def _is_sound_descriptor(source: object) -> bool:
    """Exact type, and identifiers re-validated rather than trusted."""
    if type(source) is not SourceDescriptor:
        return False
    if not isinstance(source.status, SourceStatus):
        return False
    try:
        validate_identifier("source_id", source.source_id)
        validate_identifier("workspace_id", source.workspace_id)
    except ValueError:
        return False
    return True


def _empty(context: SourceRequestContext) -> AuthorizedCatalog:
    return AuthorizedCatalog(context.tenant_id, context.policy_version)
