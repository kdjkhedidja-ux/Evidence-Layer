"""Strict validation of untrusted router output.

Every router, including the deterministic one, goes through this check. Any
violation rejects the whole output; nothing is partially cleaned, so a router
that invents or leaks a source identifier cannot influence the selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from evidence_layer.routing.catalog import AuthorizedCatalog


class RejectionReason(str, Enum):
    INVALID_TYPE = "invalid_type"
    DUPLICATE_ID = "duplicate_id"
    TOO_MANY = "too_many"
    NOT_IN_SENT_CATALOG = "not_in_sent_catalog"


@dataclass(frozen=True)
class Accepted:
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class Rejected:
    reason: RejectionReason


def validate_router_output(
    output: object,
    *,
    sent_ids: frozenset[str],
    authorized: AuthorizedCatalog,
    max_sources: int,
) -> Accepted | Rejected:
    """Accept only ``selected ⊆ sent ⊆ authorized`` without duplicates.

    Only exact ``list``/``tuple`` objects are accepted, and the output is copied
    once before any check. The accepted result is that same copy, so an object
    that changes while it is iterated cannot pass validation and then yield
    different identifiers afterwards.
    """
    if not sent_ids <= authorized.source_ids:
        raise ValueError("sent catalog exceeds the authorized catalog")
    if type(output) not in (list, tuple):
        return Rejected(RejectionReason.INVALID_TYPE)
    snapshot = tuple(output)
    if not all(type(item) is str for item in snapshot):
        return Rejected(RejectionReason.INVALID_TYPE)
    if len(set(snapshot)) != len(snapshot):
        return Rejected(RejectionReason.DUPLICATE_ID)
    if len(snapshot) > max_sources:
        return Rejected(RejectionReason.TOO_MANY)
    if any(item not in sent_ids for item in snapshot):
        return Rejected(RejectionReason.NOT_IN_SENT_CATALOG)
    return Accepted(snapshot)
