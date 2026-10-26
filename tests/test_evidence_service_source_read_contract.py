from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest

from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadError,
    SourceReadIntent,
    SourceRequestContext,
)
from evidence_service.source_read_contract import (
    DEFAULT_SOURCE_READ_LEASE_TTL,
    InMemoryReadApprovalStore,
    SourceCatalogEntry,
    SourceReadScope,
    VersionPinnedSourceReadBoundary,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
PRINCIPAL = "principal-a"
WORKSPACE = "workspace-a"


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def context(
    *,
    principal_id: str = PRINCIPAL,
    tenant_id: str = WORKSPACE,
    expires_at: datetime = NOW + timedelta(minutes=1),
) -> SourceRequestContext:
    return SourceRequestContext(
        request_id="request-a",
        operation_id="operation-a",
        principal_id=principal_id,
        tenant_id=tenant_id,
        policy_version="policy-a",
        expires_at=expires_at,
    )


class FakeCatalog:
    def __init__(self) -> None:
        self.entries = {
            "source-a": SourceCatalogEntry(
                WORKSPACE, "source-a", "immutable-v1", True
            ),
            "source-b": SourceCatalogEntry(
                WORKSPACE, "source-b", "immutable-v2", True
            ),
        }
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    def resolve(self, workspace_id: str, source_id: str):
        self.calls.append((workspace_id, source_id))
        if self.fail:
            raise RuntimeError("synthetic catalog failure")
        return self.entries.get(source_id)


class FakeAcl:
    def __init__(self) -> None:
        self.allowed = True
        self.deny_source: str | None = None
        self.deny_call: int | None = None
        self.fail_call: int | None = None
        self.fail = False
        self.calls: list[tuple[str, str, str, str, str, str]] = []

    def is_authorized(
        self,
        principal_id: str,
        workspace_id: str,
        source_id: str,
        operation: str,
        purpose: str,
        source_version: str,
    ) -> bool:
        self.calls.append(
            (
                principal_id,
                workspace_id,
                source_id,
                operation,
                purpose,
                source_version,
            )
        )
        if self.fail or self.fail_call == len(self.calls):
            raise RuntimeError("synthetic ACL failure")
        if self.deny_call == len(self.calls) or self.deny_source == source_id:
            return False
        return self.allowed


class FakeReader:
    def __init__(self, source_version: str, rows=None) -> None:
        self.source_version = source_version
        self.rows = list(rows if rows is not None else [{"text": "mock row"}])
        self.iterated = 0
        self.closed = False

    def iter_records(self):
        for row in self.rows:
            self.iterated += 1
            yield row

    def close(self) -> None:
        self.closed = True


class FakeStore:
    supports_exact_version_open = True

    def __init__(self) -> None:
        self.opened_versions: dict[str, str] = {}
        self.fail_source: str | None = None
        self.open_calls: list[tuple[str, str, str]] = []
        self.readers: list[FakeReader] = []

    def open_exact(
        self, workspace_id: str, source_id: str, source_version: str
    ) -> FakeReader:
        self.open_calls.append((workspace_id, source_id, source_version))
        if source_id == self.fail_source:
            raise OSError("synthetic exact-version open failure")
        opened_version = self.opened_versions.get(source_id, source_version)
        reader = FakeReader(opened_version)
        self.readers.append(reader)
        return reader


def make_boundary(*, clock=None, catalog=None, acl=None, store=None, approvals=None):
    clock = clock or MutableClock()
    catalog = catalog or FakeCatalog()
    acl = acl or FakeAcl()
    store = store or FakeStore()
    approvals = approvals or InMemoryReadApprovalStore()
    boundary = VersionPinnedSourceReadBoundary(
        principal_id=PRINCIPAL,
        workspace_id=WORKSPACE,
        catalog=catalog,
        acl=acl,
        store=store,
        approvals=approvals,
        clock=clock,
    )
    return boundary, clock, catalog, acl, store, approvals


def denied(result, reason: SourceReadDenialReason) -> None:
    assert isinstance(result, SourceReadDenial)
    assert result.reason is reason


def test_approval_binds_server_scope_and_catalog_version_without_client_version():
    boundary, _, _, acl, store, approvals = make_boundary()

    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )

    assert not isinstance(result, SourceReadDenial)
    assert result[0].lease.source_version == "immutable-v1"
    assert result[0].lease.expires_at == NOW + DEFAULT_SOURCE_READ_LEASE_TTL
    assert store.open_calls == [(WORKSPACE, "source-a", "immutable-v1")]
    assert acl.calls[0] == (
        PRINCIPAL,
        WORKSPACE,
        "source-a",
        "retrieve",
        "evidence_retrieval",
        "immutable-v1",
    )
    issued = tuple(approvals._issued.values())
    assert len(issued) == 1
    assert issued[0].scope == SourceReadScope(
        request_id="request-a",
        operation_id="operation-a",
        principal_id=PRINCIPAL,
        workspace_id=WORKSPACE,
        source_id="source-a",
        operation="retrieve",
        purpose="evidence_retrieval",
        source_version="immutable-v1",
        policy_version="policy-a",
    )
    for reader in result:
        reader.close()


def test_caller_expected_version_is_only_a_constraint_and_mismatch_denies():
    boundary, _, _, _, store, _ = make_boundary()

    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve", "mutable-latest")]
    )

    denied(result, SourceReadDenialReason.VERSION_MISMATCH)
    assert store.open_calls == []


def test_catalog_must_resolve_an_immutable_version_before_any_open():
    catalog = FakeCatalog()
    catalog.entries["source-a"] = replace(
        catalog.entries["source-a"], immutable_version=False
    )
    boundary, _, _, _, store, _ = make_boundary(catalog=catalog)

    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )

    denied(result, SourceReadDenialReason.VERSION_MISMATCH)
    assert store.open_calls == []


def test_store_without_exact_version_guarantee_is_denied_before_open():
    store = FakeStore()
    store.supports_exact_version_open = False
    boundary, _, _, _, _, _ = make_boundary(store=store)

    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )

    denied(result, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.open_calls == []


def test_opened_version_mismatch_closes_every_handle_and_returns_no_group():
    store = FakeStore()
    store.opened_versions["source-b"] = "changed-v3"
    boundary, _, _, _, _, _ = make_boundary(store=store)

    result = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("source-b", "retrieve"),
        ],
    )

    denied(result, SourceReadDenialReason.VERSION_MISMATCH)
    assert len(store.readers) == 2
    assert all(reader.closed for reader in store.readers)
    assert all(reader.iterated == 0 for reader in store.readers)


def test_any_acl_denial_in_batch_prevents_every_storage_open():
    acl = FakeAcl()
    acl.deny_source = "source-b"
    boundary, _, _, _, store, _ = make_boundary(acl=acl)

    result = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("source-b", "refresh"),
        ],
    )

    denied(result, SourceReadDenialReason.ACCESS_DENIED)
    assert store.open_calls == []
    assert acl.calls == [
        (
            PRINCIPAL,
            WORKSPACE,
            "source-a",
            "retrieve",
            "evidence_retrieval",
            "immutable-v1",
        ),
        (
            PRINCIPAL,
            WORKSPACE,
            "source-b",
            "refresh",
            "source_refresh",
            "immutable-v2",
        ),
    ]


def test_acl_failure_is_fail_closed_before_any_storage_open():
    acl = FakeAcl()
    acl.fail = True
    boundary, _, _, _, store, _ = make_boundary(acl=acl)

    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )

    denied(result, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.open_calls == []


def test_unknown_source_and_catalog_failure_prevent_every_storage_open():
    catalog = FakeCatalog()
    boundary, _, _, _, store, _ = make_boundary(catalog=catalog)

    unknown = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("unknown-source", "retrieve"),
        ],
    )
    catalog.fail = True
    failed = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )

    denied(unknown, SourceReadDenialReason.UNKNOWN_SOURCE)
    denied(failed, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.open_calls == []


@pytest.mark.parametrize(
    ("denied_call", "denied_source"),
    [(5, "source-b"), (6, "source-c")],
    ids=["second-member", "last-member"],
)
def test_final_acl_denial_for_later_member_prevents_every_storage_open(
    denied_call: int, denied_source: str
):
    catalog = FakeCatalog()
    catalog.entries["source-c"] = SourceCatalogEntry(
        WORKSPACE, "source-c", "immutable-v3", True
    )
    acl = FakeAcl()
    # The first three checks are batch preflight. The following checks are the
    # final batch recheck, where a later member may be rejected.
    acl.deny_call = denied_call
    boundary, _, _, _, store, _ = make_boundary(catalog=catalog, acl=acl)

    result = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("source-b", "retrieve"),
            SourceReadIntent("source-c", "retrieve"),
        ],
    )

    denied(result, SourceReadDenialReason.REVOKED)
    assert acl.calls[denied_call - 1][2] == denied_source
    assert store.open_calls == []
    assert store.readers == []


def test_final_acl_error_for_later_member_prevents_every_storage_open():
    acl = FakeAcl()
    # Both initial checks and the first final check pass; the second final
    # check fails. No source may have been opened yet.
    acl.fail_call = 4
    boundary, _, _, _, store, _ = make_boundary(acl=acl)

    result = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("source-b", "retrieve"),
        ],
    )

    denied(result, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.open_calls == []
    assert store.readers == []


def test_source_reader_stops_before_next_record_after_revocation_is_observed():
    boundary, _, _, acl, store, _ = make_boundary()
    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )
    assert not isinstance(result, SourceReadDenial)
    store.readers[0].rows = [{"id": 1}, {"id": 2}]
    records = iter(result[0].iter_records())

    assert next(records) == {"id": 1}
    acl.allowed = False
    with pytest.raises(SourceReadError) as error:
        next(records)

    assert error.value.denial.reason is SourceReadDenialReason.REVOKED
    assert store.readers[0].iterated == 1
    assert store.readers[0].closed


def test_lease_expiry_stops_stream_and_closes_reader():
    boundary, clock, _, _, store, _ = make_boundary()
    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )
    assert not isinstance(result, SourceReadDenial)
    store.readers[0].rows = [{"id": 1}, {"id": 2}]
    records = iter(result[0].iter_records())

    assert next(records) == {"id": 1}
    clock.value += DEFAULT_SOURCE_READ_LEASE_TTL + timedelta(milliseconds=1)
    with pytest.raises(SourceReadError) as error:
        next(records)

    assert error.value.denial.reason is SourceReadDenialReason.EXPIRED
    assert store.readers[0].iterated == 1
    assert store.readers[0].closed


def test_expired_context_and_identity_or_workspace_mismatch_deny():
    boundary, _, _, _, store, _ = make_boundary()

    expired = boundary.open_sources(
        context(expires_at=NOW - timedelta(seconds=1)),
        [SourceReadIntent("source-a", "retrieve")],
    )
    wrong_identity = boundary.open_sources(
        context(principal_id="principal-b"),
        [SourceReadIntent("source-a", "retrieve")],
    )
    wrong_workspace = boundary.open_sources(
        context(tenant_id="workspace-b"),
        [SourceReadIntent("source-a", "retrieve")],
    )

    denied(expired, SourceReadDenialReason.EXPIRED)
    denied(wrong_identity, SourceReadDenialReason.SCOPE_MISMATCH)
    denied(wrong_workspace, SourceReadDenialReason.SCOPE_MISMATCH)
    assert store.open_calls == []


def test_approval_cannot_be_forged_or_reused():
    store = InMemoryReadApprovalStore()
    scope = SourceReadScope(
        "request-a",
        "operation-a",
        PRINCIPAL,
        WORKSPACE,
        "source-a",
        "retrieve",
        "evidence_retrieval",
        "immutable-v1",
        "policy-a",
    )
    approval = store.issue_many(
        [scope], expires_at=NOW + DEFAULT_SOURCE_READ_LEASE_TTL
    )[0]
    forged = replace(approval, approval_id=uuid4())

    assert not store.consume_many_once_atomically([forged], now=NOW)
    assert store.consume_many_once_atomically([approval], now=NOW)
    assert not store.consume_many_once_atomically([approval], now=NOW)


def test_batch_approval_consume_does_not_partially_consume_on_forgery():
    store = InMemoryReadApprovalStore()
    scopes = [
        SourceReadScope(
            "request-a",
            "operation-a",
            PRINCIPAL,
            WORKSPACE,
            source_id,
            "retrieve",
            "evidence_retrieval",
            version,
            "policy-a",
        )
        for source_id, version in (
            ("source-a", "immutable-v1"),
            ("source-b", "immutable-v2"),
        )
    ]
    approvals = store.issue_many(
        scopes, expires_at=NOW + DEFAULT_SOURCE_READ_LEASE_TTL
    )
    forged = replace(approvals[1], approval_id=uuid4())

    assert not store.consume_many_once_atomically(
        [approvals[0], forged], now=NOW
    )
    assert store.consume_many_once_atomically([approvals[0]], now=NOW)


def test_concurrent_local_consumers_can_claim_an_approval_only_once():
    store = InMemoryReadApprovalStore()
    scope = SourceReadScope(
        "request-a",
        "operation-a",
        PRINCIPAL,
        WORKSPACE,
        "source-a",
        "retrieve",
        "evidence_retrieval",
        "immutable-v1",
        "policy-a",
    )
    approval = store.issue_many(
        [scope], expires_at=NOW + DEFAULT_SOURCE_READ_LEASE_TTL
    )[0]
    start = Barrier(2)

    def consume() -> bool:
        start.wait()
        return store.consume_many_once_atomically([approval], now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: consume(), range(2)))

    assert sorted(outcomes) == [False, True]


def test_boundary_has_no_client_supplied_approval_parameter():
    boundary, _, _, _, store, _ = make_boundary()

    with pytest.raises(TypeError):
        boundary.open_sources(
            context(),
            [SourceReadIntent("source-a", "retrieve")],
            approval=object(),
        )

    assert store.open_calls == []


def test_failed_second_open_closes_first_without_returning_partial_readers():
    store = FakeStore()
    store.fail_source = "source-b"
    boundary, _, _, _, _, _ = make_boundary(store=store)

    result = boundary.open_sources(
        context(),
        [
            SourceReadIntent("source-a", "retrieve"),
            SourceReadIntent("source-b", "retrieve"),
        ],
    )

    denied(result, SourceReadDenialReason.STORAGE_FAILURE)
    assert len(store.readers) == 1
    assert store.readers[0].closed
    assert store.readers[0].iterated == 0


def test_provider_transfer_remains_deny_all_after_mock_source_read():
    from evidence_layer.authorization import (
        DenyAllTransferAuthorizer,
        RequestOperationContext,
        TransferDenialReason,
        TransferIntent,
    )

    boundary, _, _, _, _, _ = make_boundary()
    result = boundary.open_sources(
        context(), [SourceReadIntent("source-a", "retrieve")]
    )
    assert not isinstance(result, SourceReadDenial)

    transfer = DenyAllTransferAuthorizer().authorize(
        RequestOperationContext(
            "request-a", "operation-a", PRINCIPAL, WORKSPACE, "policy-a"
        ),
        TransferIntent(
            provider="jev",
            purpose="evidence_scoring",
            source_ids=frozenset({"source-a"}),
            candidate_ids=frozenset({"candidate-a"}),
            candidate_source_pairs=frozenset({("candidate-a", "source-a")}),
            data_categories=frozenset({"candidate_text"}),
        ),
    )

    assert transfer.reason is TransferDenialReason.DENY_ALL
    for reader in result:
        reader.close()
