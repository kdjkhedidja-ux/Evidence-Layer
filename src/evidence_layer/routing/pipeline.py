"""Fail-closed routing pipeline: filter, route, validate, fall back, or stop.

Order of operations:

1. Filter candidate sources by tenant, status, and trusted policy.
2. Give the router only the authorized catalog.
3. Validate the router's output against that exact catalog.
4. On router failure, invalid output, or (optionally) an empty selection, use
   the deterministic fallback over the same authorized catalog.
5. If no safe selection exists, stop; never widen the catalog or retry blindly.

Results carry identifiers and categories only: no query text, source
descriptions, or exception messages. Routed sources are ``(tenant_id,
source_id)`` pairs: a source id need not be unique across workspaces, so a
consumer must never look a source up by id alone. A result is a routing
recommendation, not an authorization: the source-read boundary must still
re-verify access, and the result must not be used after the context expires.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from evidence_layer.routing.catalog import (
    AuthorizedCatalog,
    CatalogDenialReason,
    SourceAccessPolicy,
    context_is_well_formed,
    filter_authorized_catalog,
)
from evidence_layer.routing.contract import SourceDescriptor, validate_identifier
from evidence_layer.routing.router import DeterministicRouter, Router, RoutingRequest
from evidence_layer.routing.validation import (
    Accepted,
    RejectionReason,
    validate_router_output,
)
from evidence_layer.source_access import SourceRequestContext


class RoutingOutcome(str, Enum):
    ROUTED = "routed"
    FALLBACK_ROUTED = "fallback_routed"
    SAFE_STOP = "safe_stop"


class StopReason(str, Enum):
    INVALID_CONTEXT = "invalid_context"
    CONTEXT_EXPIRED = "context_expired"
    POLICY_ERROR = "policy_error"
    SOURCE_STORE_ERROR = "source_store_error"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    NO_AUTHORIZED_SOURCES = "no_authorized_sources"
    NO_MATCHING_SOURCE = "no_matching_source"
    NO_SAFE_FALLBACK = "no_safe_fallback"


class FallbackReason(str, Enum):
    ROUTER_ERROR = "router_error"
    INVALID_OUTPUT = "invalid_output"
    EMPTY_SELECTION = "empty_selection"


_CATALOG_STOP = {
    CatalogDenialReason.CONTEXT_EXPIRED: StopReason.CONTEXT_EXPIRED,
    CatalogDenialReason.POLICY_ERROR: StopReason.POLICY_ERROR,
    CatalogDenialReason.SOURCE_STORE_ERROR: StopReason.SOURCE_STORE_ERROR,
    CatalogDenialReason.CANDIDATE_LIMIT_EXCEEDED: StopReason.CANDIDATE_LIMIT_EXCEEDED,
    CatalogDenialReason.NO_AUTHORIZED_SOURCES: StopReason.NO_AUTHORIZED_SOURCES,
}


@dataclass(frozen=True)
class RoutedSource:
    """A selected source, always qualified by the workspace it belongs to."""

    tenant_id: str
    source_id: str

    def __post_init__(self) -> None:
        validate_identifier("tenant_id", self.tenant_id)
        validate_identifier("source_id", self.source_id)


@dataclass(frozen=True)
class RoutingResult:
    outcome: RoutingOutcome
    sources: tuple[RoutedSource, ...]
    router: str
    tenant_id: str | None
    request_id: str | None
    operation_id: str | None
    policy_version: str | None
    stop_reason: StopReason | None = None
    fallback_reason: FallbackReason | None = None
    rejection: RejectionReason | None = None


def route_query(
    request: RoutingRequest,
    context: SourceRequestContext,
    candidates: Iterable[SourceDescriptor],
    policy: SourceAccessPolicy,
    router: Router,
    *,
    fallback: Router | None = None,
    fallback_on_empty: bool = False,
) -> RoutingResult:
    """Select sources for ``request`` or stop safely.

    ``fallback`` defaults to ``DeterministicRouter``. A deterministic primary
    router has no separate fallback: its failure or empty result is a stop.
    There is no timeout around router calls yet; one is required before any
    network-backed router (such as a Decisions client) is wired in.
    """
    well_formed = context_is_well_formed(context)
    provenance = (
        (
            context.tenant_id,
            context.request_id,
            context.operation_id,
            context.policy_version,
        )
        if well_formed
        else (None, None, None, None)
    )

    def result(
        outcome: RoutingOutcome,
        router_name: str,
        source_ids: tuple[str, ...] = (),
        stop_reason: StopReason | None = None,
        fallback_reason: FallbackReason | None = None,
        rejection: RejectionReason | None = None,
    ) -> RoutingResult:
        return RoutingResult(
            outcome=outcome,
            sources=tuple(
                RoutedSource(provenance[0], source_id)  # type: ignore[arg-type]
                for source_id in source_ids
            ),
            router=router_name,
            tenant_id=provenance[0],
            request_id=provenance[1],
            operation_id=provenance[2],
            policy_version=provenance[3],
            stop_reason=stop_reason,
            fallback_reason=fallback_reason,
            rejection=rejection,
        )

    if not well_formed:
        return result(
            RoutingOutcome.SAFE_STOP,
            router.name,
            stop_reason=StopReason.INVALID_CONTEXT,
        )

    filtered = filter_authorized_catalog(context, candidates, policy)
    catalog = filtered.catalog

    if filtered.denial is not None:
        return result(
            RoutingOutcome.SAFE_STOP,
            router.name,
            stop_reason=_CATALOG_STOP[filtered.denial],
        )

    fallback_router = fallback if fallback is not None else DeterministicRouter()
    has_fallback = not isinstance(router, DeterministicRouter)

    attempt = _attempt(router, request, catalog)
    if isinstance(attempt, Accepted) and attempt.source_ids:
        return result(RoutingOutcome.ROUTED, router.name, attempt.source_ids)

    if isinstance(attempt, Accepted):
        if not (has_fallback and fallback_on_empty):
            return result(
                RoutingOutcome.SAFE_STOP,
                router.name,
                stop_reason=StopReason.NO_MATCHING_SOURCE,
            )
        reason, rejection = FallbackReason.EMPTY_SELECTION, None
    elif isinstance(attempt, FallbackReason):
        reason, rejection = attempt, None
    else:
        reason, rejection = FallbackReason.INVALID_OUTPUT, attempt

    if not has_fallback:
        return result(
            RoutingOutcome.SAFE_STOP,
            router.name,
            stop_reason=StopReason.NO_SAFE_FALLBACK,
            fallback_reason=reason,
            rejection=rejection,
        )

    second = _attempt(fallback_router, request, catalog)
    if isinstance(second, Accepted) and second.source_ids:
        return result(
            RoutingOutcome.FALLBACK_ROUTED,
            fallback_router.name,
            second.source_ids,
            fallback_reason=reason,
            rejection=rejection,
        )
    stop = (
        StopReason.NO_MATCHING_SOURCE
        if isinstance(second, Accepted)
        else StopReason.NO_SAFE_FALLBACK
    )
    return result(
        RoutingOutcome.SAFE_STOP,
        fallback_router.name,
        stop_reason=stop,
        fallback_reason=reason,
        rejection=rejection,
    )


def _attempt(
    router: Router, request: RoutingRequest, catalog: AuthorizedCatalog
) -> Accepted | FallbackReason | RejectionReason:
    """Run one router and validate it; never let its error text escape."""
    try:
        output = router.route(request, catalog)
    except Exception:
        return FallbackReason.ROUTER_ERROR
    validated = validate_router_output(
        output,
        sent_ids=catalog.source_ids,
        authorized=catalog,
        max_sources=request.max_sources,
    )
    return validated if isinstance(validated, Accepted) else validated.reason
