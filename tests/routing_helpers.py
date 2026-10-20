"""Shared factories and fakes for source-routing tests (test-only)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

import pytest

from evidence_layer.routing import (
    AuthorizedCatalog,
    Capability,
    RoutingRequest,
    RoutingResult,
    Sensitivity,
    SourceDescriptor,
    SourceStatus,
    SourceType,
)
from evidence_layer.routing import catalog as _catalog
from evidence_layer.source_access import SourceRequestContext

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze the catalog clock; the public API deliberately has no ``now``."""
    monkeypatch.setattr(_catalog, "_now", lambda: NOW)
    return NOW


def ids(result: RoutingResult) -> tuple[str, ...]:
    return tuple(source.source_id for source in result.sources)


def make_source(
    source_id: str,
    workspace_id: str = "tenant-a",
    *,
    status: SourceStatus = SourceStatus.ACTIVE,
    alias: str = "",
    description: str = "",
    tags: Iterable[str] = (),
    **overrides: object,
) -> SourceDescriptor:
    fields: dict[str, object] = {
        "source_id": source_id,
        "workspace_id": workspace_id,
        "type": SourceType.LOCAL_FILES,
        "status": status,
        "access_policy_ref": "policy-ref-1",
        "sensitivity": Sensitivity.STANDARD,
        "capabilities": frozenset({Capability.LEXICAL_SEARCH}),
        "alias": alias,
        "description": description,
        "scope_tags": tags if isinstance(tags, (str, bytes)) else frozenset(tags),
    }
    fields.update(overrides)
    return SourceDescriptor(**fields)  # type: ignore[arg-type]


def make_context(
    tenant_id: str = "tenant-a", *, expires_in: timedelta = timedelta(hours=1)
) -> SourceRequestContext:
    return SourceRequestContext(
        request_id="request-1",
        operation_id="operation-1",
        principal_id="principal-1",
        tenant_id=tenant_id,
        policy_version="policy-v1",
        expires_at=NOW + expires_in,
    )


def make_request(query: str = "billing invoices", max_sources: int = 3) -> RoutingRequest:
    return RoutingRequest(query=query, max_sources=max_sources)


class AllowAllPolicy:
    """Permissive fake: allows every candidate it is shown, plus extra ids."""

    def __init__(self, extra_ids: Iterable[str] = ()) -> None:
        self._extra = tuple(extra_ids)

    def authorized_source_ids(
        self, context: SourceRequestContext, candidate_ids: frozenset[str]
    ) -> Iterable[str]:
        return [*candidate_ids, *self._extra]


class AllowListPolicy:
    def __init__(self, allowed: Iterable[str]) -> None:
        self._allowed = tuple(allowed)

    def authorized_source_ids(
        self, context: SourceRequestContext, candidate_ids: frozenset[str]
    ) -> Iterable[str]:
        return list(self._allowed)


class RaisingPolicy:
    def authorized_source_ids(
        self, context: SourceRequestContext, candidate_ids: frozenset[str]
    ) -> Iterable[str]:
        raise RuntimeError("secret-policy-detail")


class FakeRouter:
    def __init__(self, output: object, name: str = "fake") -> None:
        self.name = name
        self._output = output
        self.seen_catalogs: list[AuthorizedCatalog] = []

    def route(
        self, request: RoutingRequest, catalog: AuthorizedCatalog
    ) -> Sequence[str]:
        self.seen_catalogs.append(catalog)
        return self._output  # type: ignore[return-value]


class RaisingRouter:
    name = "raising"

    def route(
        self, request: RoutingRequest, catalog: AuthorizedCatalog
    ) -> Sequence[str]:
        raise RuntimeError("secret-router-detail")
