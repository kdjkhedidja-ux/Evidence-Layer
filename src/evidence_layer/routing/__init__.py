"""Multi-source routing foundation: source contract, authorization filter,
deterministic router, strict output validation, and a fail-closed pipeline.

Local and deterministic only. There is no provider client, connector, or
trusted allowing policy here; ``DenyAllSourcePolicy`` is the default.
"""

from evidence_layer.routing.catalog import (
    AuthorizedCatalog,
    CatalogDenialReason,
    CatalogFilterResult,
    DenyAllSourcePolicy,
    SourceAccessPolicy,
    filter_authorized_catalog,
)
from evidence_layer.routing.contract import (
    CONTRACT_VERSION,
    Capability,
    Sensitivity,
    SourceDescriptor,
    SourceStatus,
    SourceType,
)
from evidence_layer.routing.pipeline import (
    FallbackReason,
    RoutedSource,
    RoutingOutcome,
    RoutingResult,
    StopReason,
    route_query,
)
from evidence_layer.routing.router import DeterministicRouter, Router, RoutingRequest
from evidence_layer.routing.validation import (
    Accepted,
    Rejected,
    RejectionReason,
    validate_router_output,
)

__all__ = [
    "Accepted",
    "AuthorizedCatalog",
    "CONTRACT_VERSION",
    "Capability",
    "CatalogDenialReason",
    "CatalogFilterResult",
    "DenyAllSourcePolicy",
    "DeterministicRouter",
    "FallbackReason",
    "Rejected",
    "RejectionReason",
    "RoutedSource",
    "Router",
    "RoutingOutcome",
    "RoutingRequest",
    "RoutingResult",
    "Sensitivity",
    "SourceAccessPolicy",
    "SourceDescriptor",
    "SourceStatus",
    "SourceType",
    "StopReason",
    "filter_authorized_catalog",
    "route_query",
    "validate_router_output",
]
