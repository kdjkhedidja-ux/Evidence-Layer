"""Internal, mockable contracts for version-pinned source reads.

This module is deliberately not composed by the HTTP application, MCP server,
or the normal retriever. The live runtime remains deny-all. Callers must supply
identity/workspace values already established by a trusted host; these Python
objects do not establish provenance.

Operation order is: validate the trusted context, resolve every source to an
immutable version, check ACL for the complete batch, issue and atomically
consume local approvals, check ACL again for the complete batch, then open each
exact version. No source is opened unless every final batch check passes. ACL
validation and storage opens are separate operations, not one transaction. A
revocation racing after the final batch check and before or during the opens
may not be observed before those opens. Each yielded record is checked again,
which limits further delivery after revocation is observed. The final check and
yield are not atomic, so a revocation racing between them may allow one more
record through. A blocking store read cannot be interrupted until it returns.
Already delivered records cannot be recalled.

The included approval store uses a process-local lock. It does not provide
single-use guarantees across workers, processes, or services. Production use
requires a shared atomic consume-once store and a source adapter whose exact
version-open semantics have been independently established. No such adapter
or production guarantee is supplied here. This authorization is unrelated to
provider-transfer grants such as JEV authorization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID, uuid4

from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadIntent,
    SourceReadLease,
    SourceReadResult,
    SourceReadError,
    SourceRequestContext,
)

SourceReadPurpose = Literal[
    "evidence_retrieval",
    "source_indexing",
    "source_refresh",
]

_PURPOSE_BY_OPERATION: dict[str, SourceReadPurpose] = {
    "retrieve": "evidence_retrieval",
    "index": "source_indexing",
    "refresh": "source_refresh",
}

DEFAULT_SOURCE_READ_LEASE_TTL = timedelta(seconds=30)
MAX_SOURCE_READ_LEASE_TTL = timedelta(seconds=30)


@dataclass(frozen=True)
class SourceCatalogEntry:
    """Server-resolved source metadata; version_id must be immutable."""

    workspace_id: str
    source_id: str
    version_id: str
    immutable_version: bool


@dataclass(frozen=True)
class SourceReadScope:
    """Exact server-side scope bound into one internal read approval."""

    request_id: str
    operation_id: str
    principal_id: str
    workspace_id: str
    source_id: str
    operation: str
    purpose: SourceReadPurpose
    source_version: str
    policy_version: str


@dataclass(frozen=True)
class _InternalReadApproval:
    """Opaque-by-registration handle; field values alone do not prove trust."""

    approval_id: UUID
    scope: SourceReadScope
    expires_at: datetime


class SourceCatalog(Protocol):
    def resolve(self, workspace_id: str, source_id: str) -> SourceCatalogEntry | None: ...


class SourceAccessPolicy(Protocol):
    """Server-owned ACL check, also reused between streamed records."""

    def is_authorized(
        self,
        principal_id: str,
        workspace_id: str,
        source_id: str,
        operation: str,
        purpose: SourceReadPurpose,
        source_version: str,
    ) -> bool: ...


class VersionedSourceReader(Protocol):
    @property
    def source_version(self) -> str: ...

    def iter_records(self) -> Iterable[Mapping[str, object]]: ...

    def close(self) -> None: ...


class VersionedSourceStore(Protocol):
    """Only an exact immutable-version open can satisfy this contract."""

    @property
    def supports_exact_version_open(self) -> bool: ...

    def open_exact(
        self, workspace_id: str, source_id: str, source_version: str
    ) -> VersionedSourceReader: ...


class ReadApprovalStore(Protocol):
    """The batch consume operation must be atomic within the implementation."""

    def issue_many(
        self, scopes: Sequence[SourceReadScope], *, expires_at: datetime
    ) -> tuple[_InternalReadApproval, ...]: ...

    def consume_many_once_atomically(
        self, approvals: Sequence[_InternalReadApproval], *, now: datetime
    ) -> bool: ...


class InMemoryReadApprovalStore:
    """Thread-safe consume-once store for one process, suitable for tests only.

    Its lock does not coordinate another worker, process, or service. Do not
    use it as a production replay store.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._issued: dict[UUID, _InternalReadApproval] = {}
        self._consumed: set[UUID] = set()

    def issue_many(
        self, scopes: Sequence[SourceReadScope], *, expires_at: datetime
    ) -> tuple[_InternalReadApproval, ...]:
        if not scopes or not _is_aware(expires_at):
            raise ValueError("approval scope and aware expiry are required")
        approvals = tuple(
            _InternalReadApproval(uuid4(), scope, expires_at) for scope in scopes
        )
        with self._lock:
            for approval in approvals:
                self._issued[approval.approval_id] = approval
        return approvals

    def consume_many_once_atomically(
        self, approvals: Sequence[_InternalReadApproval], *, now: datetime
    ) -> bool:
        if not approvals or not _is_aware(now):
            return False
        batch = tuple(approvals)
        if any(type(approval) is not _InternalReadApproval for approval in batch):
            return False
        ids = [approval.approval_id for approval in batch]
        if len(set(ids)) != len(ids):
            return False
        with self._lock:
            if any(
                self._issued.get(approval.approval_id) is not approval
                or approval.approval_id in self._consumed
                or approval.expires_at <= now
                for approval in batch
            ):
                return False
            self._consumed.update(ids)
            return True


class _LeasedReader:
    def __init__(
        self,
        reader: VersionedSourceReader,
        lease: SourceReadLease,
        scope: SourceReadScope,
        acl: SourceAccessPolicy,
        *,
        clock,
    ) -> None:
        self._reader = reader
        self._lease = lease
        self._scope = scope
        self._acl = acl
        self._clock = clock
        self._closed = False

    @property
    def lease(self) -> SourceReadLease:
        return self._lease

    def iter_records(self) -> Iterable[Mapping[str, object]]:
        if self._closed:
            raise SourceReadError(SourceReadDenial(SourceReadDenialReason.EXPIRED))
        try:
            iterator = iter(self._reader.iter_records())
            while True:
                self._check_lease_and_acl()
                try:
                    record = next(iterator)
                except StopIteration:
                    return
                # Check once more after storage produced the record but before
                # yielding it, so a detected revocation withholds that record.
                self._check_lease_and_acl()
                yield record
        except BaseException:
            self.close()
            raise
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._reader.close()
        except Exception:
            pass

    def _check_lease_and_acl(self) -> None:
        try:
            now = self._clock()
        except Exception as exc:
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            ) from exc
        if not _is_aware(now):
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            )
        if not isinstance(now, datetime) or not _is_aware(now):
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            )
        if self._lease.expires_at <= now.astimezone(timezone.utc):
            self.close()
            raise SourceReadError(SourceReadDenial(SourceReadDenialReason.EXPIRED))
        try:
            opened_version = self._reader.source_version
        except Exception as exc:
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            ) from exc
        if opened_version != self._scope.source_version:
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.VERSION_MISMATCH)
            )
        try:
            allowed = self._acl.is_authorized(
                self._scope.principal_id,
                self._scope.workspace_id,
                self._scope.source_id,
                self._scope.operation,
                self._scope.purpose,
                self._scope.source_version,
            )
        except Exception as exc:
            self.close()
            raise SourceReadError(
                SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            ) from exc
        if allowed is not True:
            self.close()
            raise SourceReadError(SourceReadDenial(SourceReadDenialReason.REVOKED))


class VersionPinnedSourceReadBoundary:
    """Mockable contract coordinator; the app and MCP do not compose it.

    ``principal_id`` and ``workspace_id`` must come from authenticated,
    server-selected state. This class does not verify tokens or membership.
    Catalog resolution and initial ACL checks finish before approval issuance.
    After approvals are consumed, the complete batch receives a final ACL check
    before the first storage open. Any failure in that group check returns a
    denial without opening a source. ACL checks and storage opens are not
    atomic: revocation after the final group check may race with any open. A
    failed open closes already-opened handles and returns no readers; record
    iteration starts only after the entire batch has opened successfully.
    """

    def __init__(
        self,
        *,
        principal_id: str,
        workspace_id: str,
        catalog: SourceCatalog,
        acl: SourceAccessPolicy,
        store: VersionedSourceStore,
        approvals: ReadApprovalStore,
        lease_ttl: timedelta = DEFAULT_SOURCE_READ_LEASE_TTL,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("server-verified principal is required")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValueError("server-selected workspace is required")
        if (
            type(lease_ttl) is not timedelta
            or not timedelta(0) < lease_ttl <= MAX_SOURCE_READ_LEASE_TTL
        ):
            raise ValueError("source-read lease must be positive and at most 30 seconds")
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._catalog = catalog
        self._acl = acl
        self._store = store
        self._approvals = approvals
        self._lease_ttl = lease_ttl
        self._clock = clock

    def open_sources(
        self,
        context: SourceRequestContext,
        intents: Sequence[SourceReadIntent],
    ) -> SourceReadResult:
        denial = self._validate_context_and_intents(context, intents)
        if denial is not None:
            return denial
        assert type(context) is SourceRequestContext
        assert isinstance(intents, Sequence)

        try:
            exact_version_open = (
                getattr(self._store, "supports_exact_version_open", None) is True
                and callable(getattr(self._store, "open_exact", None))
            )
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        if not exact_version_open:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        now = self._now()
        if now is None:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        context_expiry = context.expires_at.astimezone(timezone.utc)
        if context_expiry <= now:
            return SourceReadDenial(SourceReadDenialReason.EXPIRED)

        resolved: list[
            tuple[SourceReadIntent, SourceReadPurpose, SourceCatalogEntry]
        ] = []
        try:
            for intent in intents:
                entry = self._catalog.resolve(self._workspace_id, intent.source_id)
                if entry is None:
                    return SourceReadDenial(SourceReadDenialReason.UNKNOWN_SOURCE)
                if (
                    type(entry) is not SourceCatalogEntry
                    or entry.workspace_id != self._workspace_id
                    or entry.source_id != intent.source_id
                    or entry.immutable_version is not True
                    or not isinstance(entry.version_id, str)
                    or not entry.version_id.strip()
                ):
                    return SourceReadDenial(SourceReadDenialReason.VERSION_MISMATCH)
                if (
                    intent.expected_version is not None
                    and intent.expected_version != entry.version_id
                ):
                    return SourceReadDenial(SourceReadDenialReason.VERSION_MISMATCH)
                purpose = _PURPOSE_BY_OPERATION[intent.operation]
                resolved.append((intent, purpose, entry))
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        scopes: list[SourceReadScope] = []
        try:
            for intent, purpose, entry in resolved:
                allowed = self._acl.is_authorized(
                    self._principal_id,
                    self._workspace_id,
                    intent.source_id,
                    intent.operation,
                    purpose,
                    entry.version_id,
                )
                if allowed is not True:
                    return SourceReadDenial(SourceReadDenialReason.ACCESS_DENIED)
                scopes.append(
                    SourceReadScope(
                        request_id=context.request_id,
                        operation_id=context.operation_id,
                        principal_id=self._principal_id,
                        workspace_id=self._workspace_id,
                        source_id=intent.source_id,
                        operation=intent.operation,
                        purpose=purpose,
                        source_version=entry.version_id,
                        policy_version=context.policy_version,
                    )
                )
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        expires_at = min(now + self._lease_ttl, context_expiry)
        try:
            approvals = self._approvals.issue_many(scopes, expires_at=expires_at)
            if len(approvals) != len(scopes):
                return SourceReadDenial(SourceReadDenialReason.REPLAYED)
            if not self._approvals.consume_many_once_atomically(
                approvals, now=now
            ):
                return SourceReadDenial(SourceReadDenialReason.REPLAYED)
        except Exception:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)

        # Recheck the complete group before opening any member. In particular,
        # a denial for a later member must not follow an earlier member's open.
        for scope in scopes:
            try:
                allowed = self._acl.is_authorized(
                    scope.principal_id,
                    scope.workspace_id,
                    scope.source_id,
                    scope.operation,
                    scope.purpose,
                    scope.source_version,
                )
            except Exception:
                return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            if allowed is not True:
                return SourceReadDenial(SourceReadDenialReason.REVOKED)

        # ACL and storage are deliberately not assumed atomic. A revocation
        # after the complete group check can race with these sequential opens.
        readers: list[_LeasedReader] = []
        for scope in scopes:
            try:
                opened = self._store.open_exact(
                    scope.workspace_id, scope.source_id, scope.source_version
                )
            except Exception:
                _close_readers(readers)
                return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            try:
                valid_reader = callable(getattr(opened, "iter_records", None)) and callable(
                    getattr(opened, "close", None)
                )
                opened_version = getattr(opened, "source_version", None)
            except Exception:
                _close_if_possible(opened)
                _close_readers(readers)
                return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
            if not valid_reader or opened_version != scope.source_version:
                _close_if_possible(opened)
                _close_readers(readers)
                return SourceReadDenial(SourceReadDenialReason.VERSION_MISMATCH)
            try:
                lease = SourceReadLease(
                    request_id=scope.request_id,
                    operation_id=scope.operation_id,
                    principal_id=scope.principal_id,
                    tenant_id=scope.workspace_id,
                    source_id=scope.source_id,
                    operation=scope.operation,
                    source_version=scope.source_version,
                    expires_at=expires_at,
                )
                readers.append(
                    _LeasedReader(
                        opened,
                        lease,
                        scope,
                        self._acl,
                        clock=self._clock,
                    )
                )
            except Exception:
                _close_if_possible(opened)
                _close_readers(readers)
                return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        return tuple(readers)

    def _validate_context_and_intents(self, context, intents):
        if type(context) is not SourceRequestContext:
            return SourceReadDenial(SourceReadDenialReason.MISSING_CONTEXT)
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (
                    context.request_id,
                    context.operation_id,
                    context.principal_id,
                    context.tenant_id,
                    context.policy_version,
                )
            )
            or context.principal_id != self._principal_id
            or context.tenant_id != self._workspace_id
            or not isinstance(context.expires_at, datetime)
            or not _is_aware(context.expires_at)
        ):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)
        if not isinstance(intents, Sequence) or isinstance(intents, (str, bytes)):
            return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)
        if not intents:
            return SourceReadDenial(SourceReadDenialReason.UNKNOWN_SOURCE)
        for intent in intents:
            if (
                type(intent) is not SourceReadIntent
                or not isinstance(intent.source_id, str)
                or not intent.source_id.strip()
                or not isinstance(intent.operation, str)
                or intent.operation not in _PURPOSE_BY_OPERATION
                or (
                    intent.expected_version is not None
                    and (
                        not isinstance(intent.expected_version, str)
                        or not intent.expected_version.strip()
                    )
                )
            ):
                return SourceReadDenial(SourceReadDenialReason.SCOPE_MISMATCH)
        now = self._now()
        if now is None:
            return SourceReadDenial(SourceReadDenialReason.STORAGE_FAILURE)
        if context.expires_at.astimezone(timezone.utc) <= now:
            return SourceReadDenial(SourceReadDenialReason.EXPIRED)
        return None

    def _now(self) -> datetime | None:
        try:
            now = self._clock()
        except Exception:
            return None
        if not isinstance(now, datetime) or not _is_aware(now):
            return None
        return now.astimezone(timezone.utc)


def _close_if_possible(reader: object) -> None:
    close = getattr(reader, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _close_readers(readers: Sequence[_LeasedReader]) -> None:
    for reader in readers:
        reader.close()


def _is_aware(value: datetime) -> bool:
    try:
        return value.tzinfo is not None and value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False
