"""Tenant isolation and authorization-boundary tests for source routing.

The core invariant: whatever a policy, store, or router returns, a routing
result only ever names sources that belong to the caller's workspace, are
routable, and were allowed by the trusted policy. Randomized and mutation tests
check that the invariant holds and that this harness would notice a violation.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from evidence_layer.routing import (
    AuthorizedCatalog,
    CatalogDenialReason,
    DenyAllSourcePolicy,
    DeterministicRouter,
    RoutingOutcome,
    SourceStatus,
    StopReason,
    filter_authorized_catalog,
    route_query,
)
from evidence_layer.routing import pipeline
from evidence_layer.routing import Rejected, RejectionReason
from evidence_layer.routing.validation import Accepted

from routing_helpers import (
    NOW,
    AllowAllPolicy,
    AllowListPolicy,
    FakeRouter,
    RaisingPolicy,
    make_context,
    make_request,
    make_source,
)

A_SOURCES = [
    make_source("a-1", "tenant-a", tags={"billing"}),
    make_source("a-2", "tenant-a", tags={"payroll"}),
]
B_SOURCES = [
    make_source("b-1", "tenant-b", tags={"billing"}),
    make_source("b-2", "tenant-b", tags={"payroll"}),
]
MIXED_STORE = [*A_SOURCES, *B_SOURCES]


def test_default_policy_denies_everything() -> None:
    result = route_query(
        make_request(),
        make_context(),
        MIXED_STORE,
        DenyAllSourcePolicy(),
        DeterministicRouter(),
        now=NOW,
    )
    assert result.outcome is RoutingOutcome.SAFE_STOP
    assert result.stop_reason is StopReason.NO_AUTHORIZED_SOURCES
    assert result.source_ids == ()


def test_foreign_workspace_sources_are_dropped_even_when_policy_allows_them() -> None:
    filtered = filter_authorized_catalog(
        make_context("tenant-a"), MIXED_STORE, AllowAllPolicy(extra_ids=["b-1", "b-2"]), now=NOW
    )
    assert filtered.catalog.source_ids == {"a-1", "a-2"}


def test_changing_the_tenant_never_exposes_the_other_tenants_sources() -> None:
    a = filter_authorized_catalog(make_context("tenant-a"), MIXED_STORE, AllowAllPolicy(), now=NOW)
    b = filter_authorized_catalog(make_context("tenant-b"), MIXED_STORE, AllowAllPolicy(), now=NOW)
    assert a.catalog.source_ids == {"a-1", "a-2"}
    assert b.catalog.source_ids == {"b-1", "b-2"}
    assert a.catalog.source_ids.isdisjoint(b.catalog.source_ids)


def test_policy_allow_list_cannot_widen_beyond_the_tenant() -> None:
    filtered = filter_authorized_catalog(
        make_context("tenant-a"), MIXED_STORE, AllowListPolicy(["b-1", "a-1", "ghost"]), now=NOW
    )
    assert filtered.catalog.source_ids == {"a-1"}


def test_unknown_tenant_gets_nothing() -> None:
    filtered = filter_authorized_catalog(
        make_context("tenant-z"), MIXED_STORE, AllowAllPolicy(), now=NOW
    )
    assert filtered.catalog.sources == ()
    assert filtered.denial is CatalogDenialReason.NO_AUTHORIZED_SOURCES


def test_router_returning_another_tenants_source_is_rejected_then_falls_back() -> None:
    result = route_query(
        make_request("billing"),
        make_context("tenant-a"),
        MIXED_STORE,
        AllowAllPolicy(),
        FakeRouter(["b-1"]),
        now=NOW,
    )
    assert result.outcome is RoutingOutcome.FALLBACK_ROUTED
    assert result.source_ids == ("a-1",)


@pytest.mark.parametrize("status", [SourceStatus.REVOKED, SourceStatus.DISCONNECTED])
def test_unroutable_statuses_are_excluded_regardless_of_policy(status: SourceStatus) -> None:
    store = [make_source("a-gone", "tenant-a", status=status, tags={"billing"}), *A_SOURCES]
    filtered = filter_authorized_catalog(
        make_context(), store, AllowAllPolicy(extra_ids=["a-gone"]), now=NOW
    )
    assert "a-gone" not in filtered.catalog.source_ids


def test_degraded_sources_remain_routable() -> None:
    store = [make_source("a-slow", "tenant-a", status=SourceStatus.DEGRADED, tags={"billing"})]
    filtered = filter_authorized_catalog(make_context(), store, AllowAllPolicy(), now=NOW)
    assert filtered.catalog.source_ids == {"a-slow"}


def test_ambiguous_duplicate_source_ids_are_dropped() -> None:
    twin = make_source("a-1", "tenant-a", tags={"payroll"})
    filtered = filter_authorized_catalog(
        make_context(), [*A_SOURCES, twin], AllowAllPolicy(), now=NOW
    )
    assert filtered.catalog.source_ids == {"a-2"}


def test_policy_error_fails_closed_without_leaking_the_error() -> None:
    result = route_query(
        make_request(),
        make_context(),
        MIXED_STORE,
        RaisingPolicy(),
        DeterministicRouter(),
        now=NOW,
    )
    assert result.stop_reason is StopReason.POLICY_ERROR
    assert result.source_ids == ()
    assert "secret-policy-detail" not in repr(result)


def test_expired_context_stops_before_any_policy_call() -> None:
    expired = make_context(expires_in=timedelta(seconds=-1))
    result = route_query(
        make_request(), expired, MIXED_STORE, RaisingPolicy(), DeterministicRouter(), now=NOW
    )
    assert result.stop_reason is StopReason.CONTEXT_EXPIRED


def test_non_string_policy_ids_are_ignored() -> None:
    filtered = filter_authorized_catalog(
        make_context(),
        A_SOURCES,
        AllowListPolicy([1, None, ("a-1",), "a-1"]),  # type: ignore[list-item]
        now=NOW,
    )
    assert filtered.catalog.source_ids == {"a-1"}


def test_authorized_catalog_rejects_foreign_or_unroutable_sources() -> None:
    with pytest.raises(ValueError):
        AuthorizedCatalog("tenant-a", "v1", (B_SOURCES[0],))
    with pytest.raises(ValueError):
        AuthorizedCatalog(
            "tenant-a", "v1", (make_source("a-x", status=SourceStatus.REVOKED),)
        )
    with pytest.raises(ValueError):
        AuthorizedCatalog("tenant-a", "v1", (A_SOURCES[1], A_SOURCES[0]))
    with pytest.raises(ValueError):
        AuthorizedCatalog("tenant-a", "v1", (A_SOURCES[0], A_SOURCES[0]))


def test_same_tenant_duplicate_id_is_ambiguous_even_when_one_copy_is_revoked() -> None:
    stale = make_source("a-1", "tenant-a", status=SourceStatus.REVOKED, tags={"billing"})
    filtered = filter_authorized_catalog(
        make_context(), [*A_SOURCES, stale], AllowAllPolicy(), now=NOW
    )
    assert filtered.catalog.source_ids == {"a-2"}


def test_foreign_duplicate_id_does_not_make_the_tenants_source_ambiguous() -> None:
    foreign_twin = make_source("a-1", "tenant-b", tags={"billing"})
    filtered = filter_authorized_catalog(
        make_context(), [*A_SOURCES, foreign_twin], AllowAllPolicy(), now=NOW
    )
    assert filtered.catalog.source_ids == {"a-1", "a-2"}


def _failing_store():
    yield A_SOURCES[0]
    raise RuntimeError("secret-store-detail")


@pytest.mark.parametrize("store", [_failing_store, lambda: [A_SOURCES[0], {"source_id": "a-9"}]])
def test_failing_or_malformed_candidate_store_is_a_safe_stop(store) -> None:
    spy = FakeRouter(["a-1"])
    result = route_query(
        make_request(), make_context(), store(), AllowAllPolicy(), spy, now=NOW
    )
    assert result.outcome is RoutingOutcome.SAFE_STOP
    assert result.stop_reason is StopReason.SOURCE_STORE_ERROR
    assert result.source_ids == ()
    assert spy.seen_catalogs == []
    assert "secret-store-detail" not in repr(result)


@pytest.mark.parametrize(
    "context",
    [
        make_context("tenant a"),
        make_context("tenant-a\nforged-log-line"),
        make_context(""),
        None,
        "tenant-a",
        {"tenant_id": "tenant-a"},
    ],
)
def test_malformed_context_is_a_safe_stop_and_never_echoed(context) -> None:
    spy = FakeRouter(["a-1"])
    result = route_query(
        make_request(), context, MIXED_STORE, AllowAllPolicy(), spy, now=NOW
    )
    assert result.outcome is RoutingOutcome.SAFE_STOP
    assert result.stop_reason is StopReason.INVALID_CONTEXT
    assert result.source_ids == ()
    assert (result.tenant_id, result.request_id, result.operation_id, result.policy_version) == (
        None,
        None,
        None,
        None,
    )
    assert spy.seen_catalogs == []
    assert "forged-log-line" not in repr(result)


# --- randomized invariant and mutation tests ---------------------------------

TENANTS = ("tenant-a", "tenant-b", "tenant-c")
STATUSES = list(SourceStatus)
WORDS = ["billing", "payroll", "invoices", "runbooks", "audit", "ledger"]


def _universe(rng: random.Random) -> list:
    sources = []
    for i in range(rng.randint(0, 14)):
        sources.append(
            make_source(
                f"s{i}",
                rng.choice(TENANTS),
                status=rng.choice(STATUSES),
                alias=rng.choice(WORDS),
                tags={rng.choice(WORDS)},
            )
        )
    return sources


def _hostile_output(rng: random.Random, universe: list, allowed: set[str]):
    ids = [s.source_id for s in universe] + ["ghost", "", "../x"]
    kind = rng.randrange(10)
    picks = [rng.choice(ids) for _ in range(rng.randint(0, 6))]
    if kind == 0:
        return picks
    if kind == 1:
        return tuple(picks)
    if kind == 2:
        return set(picks)
    if kind == 3:
        return picks + picks[:1]
    if kind == 4:
        return picks + [None, 3]
    if kind == 5:
        return ",".join(picks)
    if kind == 6:
        return None
    if kind == 7:
        return [s.source_id for s in universe]  # everything, regardless of tenant
    if kind == 8:
        return sorted(allowed)  # valid ids, but possibly more than the cap
    return _ShiftyList(sorted(allowed)[:1], [s.source_id for s in universe] + ["ghost"])


class _ShiftyList(list):
    """Valid on early iterations, hostile afterwards."""

    def __init__(self, safe, hostile):
        super().__init__(safe)
        self._hostile = hostile
        self._n = 0

    def __iter__(self):
        self._n += 1
        return super().__iter__() if self._n <= 3 else iter(self._hostile)


class _Raises:
    name = "raises"

    def route(self, request, catalog):
        raise RuntimeError("boom")


def _run_fuzz(seed: int, iterations: int) -> list[str]:
    """Return a description of every invariant violation found."""
    rng = random.Random(seed)
    violations: list[str] = []
    for _ in range(iterations):
        universe = _universe(rng)
        tenant = rng.choice(TENANTS)
        policy = AllowListPolicy(
            rng.sample([s.source_id for s in universe], k=rng.randint(0, len(universe)))
            + ["ghost"]
            if universe
            else ["ghost"]
        )
        allowed = {
            s.source_id
            for s in universe
            if s.workspace_id == tenant
            and s.status in (SourceStatus.ACTIVE, SourceStatus.DEGRADED)
            and s.source_id in set(policy.authorized_source_ids(None, frozenset()))
        }
        router = _Raises() if rng.random() < 0.15 else FakeRouter(_hostile_output(rng, universe, allowed))
        max_sources = rng.randint(1, 5)
        result = route_query(
            make_request("billing payroll ledger", max_sources=max_sources),
            make_context(tenant),
            universe,
            policy,
            router,
            fallback_on_empty=rng.random() < 0.5,
            now=NOW,
        )
        ids = result.source_ids
        if not set(ids) <= allowed:
            violations.append(f"unauthorized ids {set(ids) - allowed}")
        if len(set(ids)) != len(ids):
            violations.append("duplicate ids")
        if len(ids) > max_sources:
            violations.append("too many ids")
        if result.outcome is RoutingOutcome.SAFE_STOP and ids:
            violations.append("safe stop with ids")
        if result.outcome is not RoutingOutcome.SAFE_STOP and not ids:
            violations.append("routed with no ids")
        if result.tenant_id != tenant:
            violations.append("tenant mismatch")
    return violations


def test_invariant_holds_under_randomized_hostile_inputs() -> None:
    for seed in (20261020, 1, 2, 3):
        assert _run_fuzz(seed, 400) == []


def test_harness_detects_a_validator_that_stops_validating(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "validate_router_output",
        lambda output, **kwargs: Accepted(tuple(output) if output else ()),
    )
    assert _run_fuzz(20261020, 400) != []


def test_harness_detects_a_validator_that_ignores_the_source_cap(monkeypatch) -> None:
    real = pipeline.validate_router_output

    def lenient(output, *, sent_ids, authorized, max_sources):
        return real(
            output,
            sent_ids=sent_ids,
            authorized=authorized,
            max_sources=max_sources + 100,
        )

    monkeypatch.setattr(pipeline, "validate_router_output", lenient)
    assert _run_fuzz(20261020, 400) != []


def test_harness_detects_the_original_iteration_tampering_flaw(monkeypatch) -> None:
    """The validator as first written: validated the object, then re-read it."""

    def original(output, *, sent_ids, authorized, max_sources):
        if not isinstance(output, (list, tuple)):
            return Rejected(RejectionReason.INVALID_TYPE)
        if not all(type(item) is str for item in output):
            return Rejected(RejectionReason.INVALID_TYPE)
        if len(set(output)) != len(output):
            return Rejected(RejectionReason.DUPLICATE_ID)
        if len(output) > max_sources:
            return Rejected(RejectionReason.TOO_MANY)
        if any(item not in sent_ids for item in output):
            return Rejected(RejectionReason.NOT_IN_SENT_CATALOG)
        return Accepted(tuple(output))

    monkeypatch.setattr(pipeline, "validate_router_output", original)
    assert _run_fuzz(20261020, 400) != []
