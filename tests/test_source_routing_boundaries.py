"""Boundary tests: what the routing layer does and does not defend.

Each test states a boundary explicitly, including the ones that are NOT
defended here (a forged ``SourceRequestContext``), so the assumption is visible
instead of implied.
"""

from __future__ import annotations

import inspect
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from evidence_layer.routing import (
    DenyAllSourcePolicy,
    DeterministicRouter,
    RoutedSource,
    RoutingOutcome,
    Sensitivity,
    SourceDescriptor,
    SourceStatus,
    SourceType,
    StopReason,
    filter_authorized_catalog,
    route_query,
)
from evidence_layer.routing import catalog as catalog_module
from evidence_layer.routing.contract import Capability
from evidence_layer.source_access import SourceRequestContext

from routing_helpers import (
    NOW,
    AllowAllPolicy,
    FakeRouter,
    frozen_clock,
    ids,
    make_context,
    make_request,
    make_source,
)

A = make_source("a-1", "tenant-a", tags={"billing"})
B = make_source("b-1", "tenant-b", tags={"billing"})


def real_context(tenant_id: str, *, expires_in: timedelta) -> SourceRequestContext:
    return SourceRequestContext(
        request_id="request-1",
        operation_id="operation-1",
        principal_id="principal-1",
        tenant_id=tenant_id,
        policy_version="policy-v1",
        expires_at=datetime.now(timezone.utc) + expires_in,
    )


# --- clock ---------------------------------------------------------------------


def test_clock_is_not_part_of_the_public_api() -> None:
    assert "now" not in inspect.signature(route_query).parameters
    assert "now" not in inspect.signature(filter_authorized_catalog).parameters
    with pytest.raises(TypeError):
        route_query(  # type: ignore[call-arg]
            make_request("billing"),
            real_context("tenant-a", expires_in=timedelta(minutes=5)),
            [A],
            AllowAllPolicy(),
            DeterministicRouter(),
            now=NOW - timedelta(days=30),
        )


def test_expiry_uses_the_real_clock_and_cannot_be_resurrected() -> None:
    expired = real_context("tenant-a", expires_in=timedelta(hours=-1))
    result = route_query(
        make_request("billing"), expired, [A], AllowAllPolicy(), DeterministicRouter()
    )
    assert result.stop_reason is StopReason.CONTEXT_EXPIRED
    live = real_context("tenant-a", expires_in=timedelta(minutes=5))
    result = route_query(
        make_request("billing"), live, [A], AllowAllPolicy(), DeterministicRouter()
    )
    assert result.outcome is RoutingOutcome.ROUTED


# --- trust source of the context (the documented, undefended assumption) -------


@pytest.mark.usefixtures("frozen_clock")
def test_forged_context_is_not_defended_by_this_layer() -> None:
    """A context minted for another tenant is believed; only the policy can stop it.

    This is the stated trust assumption: tenant isolation depends on a trusted
    host building the context from verified membership. If that is skipped, an
    allowing policy that ignores the principal hands over the victim's sources.
    """
    forged = make_context("tenant-b")  # principal-1 is not a member of tenant-b
    result = route_query(
        make_request("billing"), forged, [A, B], AllowAllPolicy(), DeterministicRouter()
    )
    assert result.sources == (RoutedSource("tenant-b", "b-1"),)


@pytest.mark.usefixtures("frozen_clock")
def test_a_membership_aware_policy_is_what_stops_a_forged_principal() -> None:
    members = {("principal-9", "tenant-b")}

    class MembershipPolicy:
        def authorized_source_ids(self, context, candidate_ids):
            if (context.principal_id, context.tenant_id) not in members:
                return ()
            return candidate_ids

    result = route_query(
        make_request("billing"),
        make_context("tenant-b"),
        [A, B],
        MembershipPolicy(),
        DeterministicRouter(),
    )
    assert result.stop_reason is StopReason.NO_AUTHORIZED_SOURCES


@pytest.mark.usefixtures("frozen_clock")
def test_the_only_shipped_policy_denies_even_a_forged_context() -> None:
    result = route_query(
        make_request("billing"),
        make_context("tenant-b"),
        [A, B],
        DenyAllSourcePolicy(),
        DeterministicRouter(),
    )
    assert result.stop_reason is StopReason.NO_AUTHORIZED_SOURCES


@pytest.mark.usefixtures("frozen_clock")
def test_context_subclasses_are_rejected() -> None:
    class ShiftyContext(SourceRequestContext):
        pass

    ctx = ShiftyContext(
        "request-1", "operation-1", "principal-1", "tenant-a", "policy-v1", NOW + timedelta(hours=1)
    )
    spy = FakeRouter(["a-1"])
    result = route_query(make_request("billing"), ctx, [A], AllowAllPolicy(), spy)
    assert result.stop_reason is StopReason.INVALID_CONTEXT
    assert spy.seen_catalogs == []


@pytest.mark.usefixtures("frozen_clock")
@pytest.mark.parametrize("expires_at", [None, "2099-01-01", 4102444800, object()])
def test_non_datetime_expiry_is_a_safe_stop_not_an_exception(expires_at: object) -> None:
    ctx = SourceRequestContext(
        "request-1", "operation-1", "principal-1", "tenant-a", "policy-v1", expires_at  # type: ignore[arg-type]
    )
    spy = FakeRouter(["a-1"])
    result = route_query(make_request("billing"), ctx, [A], AllowAllPolicy(), spy)
    assert result.outcome is RoutingOutcome.SAFE_STOP
    assert result.stop_reason is StopReason.INVALID_CONTEXT
    assert result.tenant_id is None
    assert spy.seen_catalogs == []


# --- untrusted store output ------------------------------------------------------


def _descriptor_kwargs(source_id: str = "a-9") -> dict:
    return {
        "source_id": source_id,
        "workspace_id": "tenant-a",
        "type": SourceType.LOCAL_FILES,
        "status": SourceStatus.ACTIVE,
        "access_policy_ref": "policy-ref-1",
        "sensitivity": Sensitivity.STANDARD,
        "capabilities": frozenset({Capability.LEXICAL_SEARCH}),
        "alias": "billing",
        "scope_tags": frozenset({"billing"}),
    }


@pytest.mark.usefixtures("frozen_clock")
def test_descriptor_subclass_that_skips_validation_is_a_store_error() -> None:
    class Loose(SourceDescriptor):
        def __post_init__(self) -> None:
            pass

    loose = Loose(**_descriptor_kwargs("a-9\nFORGED LOG LINE"))
    result = route_query(
        make_request("billing"), make_context(), [A, loose], AllowAllPolicy(), DeterministicRouter()
    )
    assert result.stop_reason is StopReason.SOURCE_STORE_ERROR
    assert result.sources == ()
    assert "FORGED" not in repr(result)


@pytest.mark.usefixtures("frozen_clock")
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "a-9\nFORGED"),
        ("source_id", "../secret"),
        ("workspace_id", "tenant a"),
        ("status", "active"),
    ],
)
def test_identifiers_are_revalidated_on_exact_descriptors(field: str, value: object) -> None:
    tampered = SourceDescriptor(**_descriptor_kwargs())
    object.__setattr__(tampered, field, value)  # simulates a bypass such as unpickling
    result = route_query(
        make_request("billing"), make_context(), [tampered], AllowAllPolicy(), DeterministicRouter()
    )
    assert result.stop_reason is StopReason.SOURCE_STORE_ERROR
    assert result.sources == ()


@pytest.mark.usefixtures("frozen_clock")
def test_candidate_limit_stops_an_endless_store(monkeypatch) -> None:
    result = route_query(
        make_request("billing"),
        make_context(),
        itertools.repeat(A),
        AllowAllPolicy(),
        DeterministicRouter(),
    )
    assert result.stop_reason is StopReason.CANDIDATE_LIMIT_EXCEEDED
    assert result.sources == ()


@pytest.mark.usefixtures("frozen_clock")
def test_candidate_limit_boundary(monkeypatch) -> None:
    monkeypatch.setattr(catalog_module, "MAX_CANDIDATES", 3)
    sources = [make_source(f"a-{i}", "tenant-a", tags={"billing"}) for i in range(4)]
    ok = route_query(
        make_request("billing"), make_context(), sources[:3], AllowAllPolicy(), DeterministicRouter()
    )
    assert ok.outcome is RoutingOutcome.ROUTED
    over = route_query(
        make_request("billing"), make_context(), sources, AllowAllPolicy(), DeterministicRouter()
    )
    assert over.stop_reason is StopReason.CANDIDATE_LIMIT_EXCEEDED


@pytest.mark.usefixtures("frozen_clock")
def test_an_endless_policy_output_is_a_policy_error() -> None:
    class EndlessPolicy:
        def authorized_source_ids(self, context, candidate_ids):
            return itertools.repeat("a-1")

    result = route_query(
        make_request("billing"), make_context(), [A], EndlessPolicy(), DeterministicRouter()
    )
    assert result.stop_reason is StopReason.POLICY_ERROR
    assert result.sources == ()


@pytest.mark.usefixtures("frozen_clock")
def test_a_defect_in_this_module_is_not_masked_as_a_store_error(monkeypatch) -> None:
    def broken(name, value):
        raise RuntimeError("defect, not a store failure")

    monkeypatch.setattr(catalog_module, "validate_identifier", broken)
    with pytest.raises(RuntimeError):
        route_query(
            make_request("billing"), make_context(), [A], AllowAllPolicy(), DeterministicRouter()
        )


# --- identifiers across workspaces ------------------------------------------------


@pytest.mark.usefixtures("frozen_clock")
def test_a_source_id_shared_by_two_workspaces_is_always_tenant_qualified() -> None:
    a1 = make_source("shared-1", "tenant-a", tags={"billing"})
    b1 = make_source("shared-1", "tenant-b", tags={"billing"})
    store = [a1, b1]
    for_a = route_query(
        make_request("billing"), make_context("tenant-a"), store, AllowAllPolicy(), DeterministicRouter()
    )
    for_b = route_query(
        make_request("billing"), make_context("tenant-b"), store, AllowAllPolicy(), DeterministicRouter()
    )
    assert ids(for_a) == ids(for_b) == ("shared-1",)
    assert for_a.sources == (RoutedSource("tenant-a", "shared-1"),)
    assert for_b.sources == (RoutedSource("tenant-b", "shared-1"),)
    assert for_a.sources != for_b.sources


@pytest.mark.parametrize(
    "tenant_id,source_id",
    [("", "a"), ("tenant a", "a"), ("tenant-a", "a\nb"), ("tenant-a", "../x"), (None, "a"), ("tenant-a", 3)],
)
def test_routed_source_validates_both_identifiers(tenant_id, source_id) -> None:
    with pytest.raises(ValueError):
        RoutedSource(tenant_id, source_id)
