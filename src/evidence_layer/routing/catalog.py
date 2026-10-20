"""Authorization filtering that runs before any router sees a source.

The trusted tenant comes from a ``SourceRequestContext`` that a trusted host
built from verified membership. A router only ever receives an
``AuthorizedCatalog``; it never sees sources from other workspaces, sources
whose status is not routable, or sources the policy did not allow.

This phase ships no allowing policy. ``DenyAllSourcePolicy`` is the default,
and any allowing implementation belongs to a future trusted host.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from evidence_layer.routing.contract import (
    ROUTABLE_STATUSES,
    SourceDescriptor,
    validate_identifier,
)
from evidence_layer.source_access import SourceRequestContext, context_is_expired


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
    NO_AUTHORIZED_SOURCES = "no_authorized_sources"


def context_is_well_formed(context: object) -> bool:
    """True when the context carries identifiers safe to log and bind to.

    This checks shape only; it does not establish that a trusted host built the
    context. Callers must still obtain the context from verified membership.
    """
    if not isinstance(context, SourceRequestContext):
        return False
    try:
        for name in ("tenant_id", "request_id", "operation_id", "policy_version"):
            validate_identifier(name, getattr(context, name))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class AuthorizedCatalog:
    """Sources one tenant may route over, ordered by ``source_id``."""

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
    *,
    now: datetime | None = None,
) -> CatalogFilterResult:
    """Return only sources this tenant may route over; fail closed on any doubt.

    Candidates may come from a store that mixes workspaces. Sources owned by
    another workspace, or whose status is not routable, are dropped regardless
    of what the policy says. An identifier that appears more than once for the
    tenant, whatever its status, is ambiguous and dropped. Identifiers the
    policy returns that are not candidates are ignored. A failing candidate
    store, or a candidate that is not a ``SourceDescriptor``, is a denial.

    ``context`` must be well formed (``context_is_well_formed``); the routing
    pipeline checks this and stops safely, while this function raises
    ``ValueError`` on an unusable tenant identifier.
    """
    if context_is_expired(context, now=now):
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.CONTEXT_EXPIRED
        )

    eligible: dict[str, SourceDescriptor] = {}
    try:
        seen: set[str] = set()
        ambiguous: set[str] = set()
        for source in candidates:
            if not isinstance(source, SourceDescriptor):
                raise TypeError("candidate is not a SourceDescriptor")
            if source.workspace_id != context.tenant_id:
                continue
            if source.source_id in seen:
                ambiguous.add(source.source_id)
            seen.add(source.source_id)
            if source.status in ROUTABLE_STATUSES:
                eligible[source.source_id] = source
        for source_id in ambiguous:
            eligible.pop(source_id, None)
    except Exception:
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.SOURCE_STORE_ERROR
        )
    try:
        allowed = frozenset(
            source_id
            for source_id in policy.authorized_source_ids(
                context, frozenset(eligible)
            )
            if isinstance(source_id, str)
        )
    except Exception:
        return CatalogFilterResult(_empty(context), CatalogDenialReason.POLICY_ERROR)

    sources = tuple(eligible[source_id] for source_id in sorted(eligible.keys() & allowed))
    if not sources:
        return CatalogFilterResult(
            _empty(context), CatalogDenialReason.NO_AUTHORIZED_SOURCES
        )
    return CatalogFilterResult(
        AuthorizedCatalog(context.tenant_id, context.policy_version, sources)
    )


def _empty(context: SourceRequestContext) -> AuthorizedCatalog:
    return AuthorizedCatalog(context.tenant_id, context.policy_version)
