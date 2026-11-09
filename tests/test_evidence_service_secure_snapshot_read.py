from __future__ import annotations

import asyncio
import gzip
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from evidence_layer.source_access import (
    SourceReadDenialReason,
    SourceReadError,
)
from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationRejected,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
)
from evidence_service.secure_snapshot_read import (
    InMemoryImmutableObjectStore,
    InMemorySnapshotCatalog,
    LocalSupabaseStorageObjectStore,
    SecureSnapshotReadPath,
    SnapshotObjectUnavailable,
    SnapshotPublisher,
    SnapshotState,
    SourceSnapshotManifest,
    snapshot_object_key,
)
from evidence_service.tenant_context import WorkspaceMembership

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
ISSUER = "http://127.0.0.1:55321/auth/v1"
PRINCIPAL = Principal(
    ISSUER, UUID("10000000-0000-4000-8000-000000000001")
)
WORKSPACE_A = UUID("a1000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("b3000000-0000-4000-8000-000000000003")
SOURCE_A = UUID("c1000000-0000-4000-8000-000000000001")
VERSION_A = UUID("d1000000-0000-4000-8000-000000000001")
TOKEN = "synthetic-local-user-token"
OWNER = UUID("e1000000-0000-4000-8000-000000000001")
CONTENT = b"verified snapshot bytes\n"


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeVerifier:
    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock
        self.principal = PRINCIPAL
        self.lifetime = timedelta(minutes=5)
        self.reject = False
        self.calls = 0

    async def verify(self, token: str) -> Principal:
        return (await self.verify_access_token(token)).principal

    async def verify_access_token(self, token: str) -> VerifiedAccessToken:
        self.calls += 1
        if token != TOKEN or self.reject:
            raise AuthenticationRejected
        return VerifiedAccessToken(self.principal, self.clock.value + self.lifetime)


class MutableWorkspaceDirectory:
    def __init__(self) -> None:
        self.active: set[tuple[UUID, UUID]] = {(PRINCIPAL.subject, WORKSPACE_A)}
        self.calls: list[tuple[UUID, UUID]] = []
        self.on_lookup = None

    async def ensure_personal_workspace(self, principal, access_token):
        raise AssertionError("not used")

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        self.calls.append((principal.subject, workspace_id))
        if self.on_lookup is not None:
            self.on_lookup(len(self.calls))
        if (principal.subject, workspace_id) not in self.active:
            return None
        return WorkspaceMembership(workspace_id, "Local workspace", "owner")


class MutableAcl:
    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[tuple[UUID, UUID, UUID, UUID]] = []
        self.deny_after: int | None = None

    async def may_read(self, principal_id, workspace_id, source_id, version_id):
        self.calls.append((principal_id, workspace_id, source_id, version_id))
        if self.deny_after == len(self.calls):
            return False
        return self.allowed


def manifest(
    *,
    workspace_id: UUID = WORKSPACE_A,
    source_id: UUID = SOURCE_A,
    version_id: UUID = VERSION_A,
    content: bytes = CONTENT,
) -> SourceSnapshotManifest:
    import hashlib

    return SourceSnapshotManifest(
        workspace_id=workspace_id,
        source_id=source_id,
        owner_id=OWNER,
        version_id=version_id,
        object_key=snapshot_object_key(workspace_id, source_id, version_id),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        state=SnapshotState.PUBLISHED,
        created_at=NOW,
    )


def setup_path(*, content: bytes = CONTENT, account_lifecycle_check=None):
    clock = MutableClock()
    verifier = FakeVerifier(clock)
    directory = MutableWorkspaceDirectory()
    catalog = InMemorySnapshotCatalog()
    acl = MutableAcl()
    store = InMemoryImmutableObjectStore()
    item = manifest(content=content)
    store._objects[item.object_key] = content
    asyncio.run(catalog.publish_if_absent(item))
    path = SecureSnapshotReadPath(
        verifier=verifier,
        workspace_directory=directory,
        catalog=catalog,
        acl=acl,
        store=store,
        account_lifecycle_check=account_lifecycle_check,
        clock=clock,
    )
    auth = AuthenticatedRequest(PRINCIPAL, TOKEN)
    return path, auth, clock, verifier, directory, catalog, acl, store, item


def read(path, auth, *, workspace_id=WORKSPACE_A, source_id=SOURCE_A, version_id=VERSION_A):
    return asyncio.run(
        path.read(
            auth,
            workspace_id=workspace_id,
            source_id=source_id,
            version_id=version_id,
        )
    )


def assert_denied(operation, reason: SourceReadDenialReason) -> None:
    with pytest.raises(SourceReadError) as error:
        operation()
    assert error.value.denial.reason is reason


def test_authenticated_member_gets_only_requested_version_after_full_integrity_check():
    path, auth, _, verifier, directory, _, acl, store, item = setup_path()
    newer = manifest(version_id=uuid4(), content=b"newer content")
    asyncio.run(path._catalog.publish_if_absent(newer))
    store._objects[newer.object_key] = b"newer content"

    result = read(path, auth, version_id=VERSION_A)

    assert result.content == CONTENT
    assert result.manifest.version_id == VERSION_A
    assert result.manifest.object_key == item.object_key
    assert store.opened == [item.object_key]
    assert verifier.calls >= 2
    assert len(directory.calls) >= 2
    assert len(acl.calls) >= 2


def test_workspace_switch_cannot_resolve_or_open_another_workspaces_source():
    path, auth, _, _, _, _, _, store, _ = setup_path()
    # Even an active member in B cannot use a source/version cataloged in A.
    path._directory.active.add((PRINCIPAL.subject, WORKSPACE_B))

    assert_denied(
        lambda: read(path, auth, workspace_id=WORKSPACE_B),
        SourceReadDenialReason.VERSION_MISMATCH,
    )
    assert store.opened == []


def test_unauthed_source_acl_denies_before_object_open():
    path, auth, _, _, _, _, acl, store, _ = setup_path()
    acl.allowed = False

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.ACCESS_DENIED)
    assert store.opened == []


def test_revoked_membership_after_object_read_withholds_complete_payload():
    path, auth, _, _, directory, _, _, store, _ = setup_path()

    def revoke_membership(key, value):
        directory.active.clear()
        return value

    store.read_override = revoke_membership

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.REVOKED)
    assert len(store.opened) == 1


def test_expired_read_context_during_storage_read_withholds_complete_payload():
    path, auth, clock, _, _, _, _, store, _ = setup_path()

    def expire(key, value):
        clock.value += timedelta(seconds=31)
        return value

    store.read_override = expire
    assert_denied(lambda: read(path, auth), SourceReadDenialReason.EXPIRED)


def test_expired_token_denies_before_catalog_or_storage_access():
    path, auth, clock, verifier, _, catalog, _, store, _ = setup_path()
    verifier.lifetime = timedelta(seconds=-1)

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.EXPIRED)
    assert catalog._entries
    assert store.opened == []


def test_missing_requested_version_denies_without_opening_latest_or_any_object():
    path, auth, _, _, _, _, _, store, _ = setup_path()

    assert_denied(
        lambda: read(path, auth, version_id=uuid4()),
        SourceReadDenialReason.VERSION_MISMATCH,
    )
    assert store.opened == []


def test_sha256_mismatch_withholds_all_content():
    path, auth, _, _, _, _, _, store, item = setup_path()
    store._objects[item.object_key] = b"altered snapshot bytes\n"

    assert_denied(
        lambda: read(path, auth),
        SourceReadDenialReason.INVALID_SOURCE_DATA,
    )


def test_truncated_content_withholds_all_content():
    path, auth, _, _, _, _, _, store, item = setup_path()
    store._objects[item.object_key] = CONTENT[:-1]

    assert_denied(
        lambda: read(path, auth),
        SourceReadDenialReason.INVALID_SOURCE_DATA,
    )


def test_acl_revoked_after_download_but_before_delivery_withholds_payload():
    path, auth, _, _, _, _, acl, store, _ = setup_path()
    acl.deny_after = 3

    assert_denied(
        lambda: read(path, auth),
        SourceReadDenialReason.ACCESS_DENIED,
    )
    assert len(store.opened) == 1


def test_lifecycle_failure_at_storage_boundary_prevents_object_open():
    checks = 0

    async def lifecycle_check(_authenticated_request):
        nonlocal checks
        checks += 1
        if checks == 8:
            raise HTTPException(status_code=503, detail="Account status is unavailable")

    path, auth, _, _, _, catalog, _, store, _ = setup_path(
        account_lifecycle_check=lifecycle_check
    )

    with pytest.raises(HTTPException) as error:
        read(path, auth)

    assert error.value.status_code == 503
    assert checks == 8
    assert catalog._entries
    assert store.opened == []


def test_lifecycle_failure_at_final_snapshot_boundary_withholds_result():
    checks = 0

    async def lifecycle_check(_authenticated_request):
        nonlocal checks
        checks += 1
        if checks == 11:
            raise HTTPException(status_code=403, detail="Account unavailable")

    path, auth, _, _, _, _, _, store, _ = setup_path(
        account_lifecycle_check=lifecycle_check
    )

    with pytest.raises(HTTPException) as error:
        read(path, auth)

    assert error.value.status_code == 403
    assert checks == 11
    assert len(store.opened) == 1


def test_publish_verifies_complete_object_before_manifest_is_visible():
    async def run():
        catalog = InMemorySnapshotCatalog()
        store = InMemoryImmutableObjectStore()
        store.read_override = lambda key, value: value[:-1]
        publisher = SnapshotPublisher(catalog=catalog, store=store)
        version_id = uuid4()
        with pytest.raises(SourceReadError) as error:
            await publisher.publish(
                workspace_id=WORKSPACE_A,
                source_id=SOURCE_A,
                owner_id=OWNER,
                version_id=version_id,
                content=CONTENT,
                access_token=TOKEN,
            )
        assert error.value.denial.reason is SourceReadDenialReason.INVALID_SOURCE_DATA
        assert await catalog.resolve_version(WORKSPACE_A, SOURCE_A, version_id) is None

    asyncio.run(run())


def test_write_once_publish_rejects_concurrent_replacement_of_same_version():
    async def publish(catalog, store, payload):
        return await SnapshotPublisher(catalog=catalog, store=store).publish(
            workspace_id=WORKSPACE_A,
            source_id=SOURCE_A,
            owner_id=OWNER,
            version_id=VERSION_A,
            content=payload,
            access_token=TOKEN,
        )

    async def run():
        catalog = InMemorySnapshotCatalog()
        store = InMemoryImmutableObjectStore()
        results = await asyncio.gather(
            publish(catalog, store, b"first payload"),
            publish(catalog, store, b"replacement payload"),
            return_exceptions=True,
        )
        successful = [item for item in results if isinstance(item, SourceSnapshotManifest)]
        failed = [item for item in results if isinstance(item, SourceReadError)]
        assert len(successful) == len(failed) == 1
        manifest_value = await catalog.resolve_version(WORKSPACE_A, SOURCE_A, VERSION_A)
        assert manifest_value == successful[0]
        stored = await store.read_exact(manifest_value.object_key, access_token=TOKEN)
        assert stored in {b"first payload", b"replacement payload"}
        assert len(await _published_entries(catalog)) == 1

    asyncio.run(run())


async def _published_entries(catalog):
    return list(catalog._entries.values())


def test_local_storage_adapter_refuses_non_loopback_targets():
    for url in (
        "https://example.supabase.co",
        "http://192.0.2.10:55321",
        "http://localhost:55321/path",
    ):
        with pytest.raises(ValueError):
            LocalSupabaseStorageObjectStore(
                base_url=url,
                bucket="test-only",
                publishable_key="sb_publishable_local-test-key",
            )


def test_local_storage_adapter_rejects_path_traversal_before_http_request():
    async def run():
        store = LocalSupabaseStorageObjectStore(
            base_url="http://127.0.0.1:55321",
            bucket="test-only",
            publishable_key="sb_publishable_local-test-key",
        )
        with pytest.raises(ValueError):
            await store.read_exact("../../outside", access_token=TOKEN)

    asyncio.run(run())


def test_adapter_rejects_service_role_like_api_key():
    with pytest.raises(ValueError):
        LocalSupabaseStorageObjectStore(
            base_url="http://127.0.0.1:55321",
            bucket="test-only",
            publishable_key="service_role",
        )


def _install(path, item, content=CONTENT):
    # Always filed under the requested (A, SOURCE_A, VERSION_A) key, as a
    # faulty catalog would return it, whatever the record itself claims.
    path._catalog._entries[(WORKSPACE_A, SOURCE_A, VERSION_A)] = item
    path._store._objects[item.object_key] = content


def test_unpublished_staged_manifest_is_never_readable():
    from dataclasses import replace

    path, auth, _, _, _, _, _, store, item = setup_path()
    _install(path, replace(item, state=SnapshotState.STAGED))

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.VERSION_MISMATCH)
    assert store.opened == []


def test_manifest_for_another_workspace_or_key_is_rejected_before_open():
    from dataclasses import replace

    for tampered in (
        lambda m: replace(m, workspace_id=WORKSPACE_B),
        lambda m: replace(m, source_id=uuid4()),
        lambda m: replace(m, version_id=uuid4()),
        lambda m: replace(m, object_key="workspace/other/source/x/version/y"),
    ):
        path, auth, _, _, _, _, _, store, item = setup_path()
        _install(path, tampered(item))
        assert_denied(lambda: read(path, auth), SourceReadDenialReason.VERSION_MISMATCH)
        assert store.opened == []


def test_malformed_or_oversized_manifest_metadata_is_rejected_before_open():
    from dataclasses import replace

    for tampered in (
        lambda m: replace(m, sha256="A" * 64),
        lambda m: replace(m, sha256="short"),
        lambda m: replace(m, size_bytes=-1),
        lambda m: replace(m, size_bytes=8 * 1024 * 1024 + 1),
        lambda m: replace(m, size_bytes=True),
    ):
        path, auth, _, _, _, _, _, store, item = setup_path()
        _install(path, tampered(item))
        assert_denied(lambda: read(path, auth), SourceReadDenialReason.INVALID_SOURCE_DATA)
        assert store.opened == []


def test_extended_content_withholds_all_content():
    path, auth, _, _, _, _, _, store, item = setup_path()
    store._objects[item.object_key] = CONTENT + b"extra"

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.INVALID_SOURCE_DATA)


def test_non_bytes_storage_response_withholds_all_content():
    path, auth, _, _, _, _, _, store, item = setup_path()
    store.read_override = lambda key, value: value.decode()

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.INVALID_SOURCE_DATA)


def test_token_for_a_different_principal_than_the_request_claims_is_denied():
    path, auth, _, verifier, _, _, _, store, _ = setup_path()
    verifier.principal = Principal(ISSUER, uuid4())

    assert_denied(lambda: read(path, auth), SourceReadDenialReason.SCOPE_MISMATCH)
    assert store.opened == []


def test_uploaded_object_without_published_record_is_unreadable_and_not_replaceable():
    class FailingCatalog(InMemorySnapshotCatalog):
        async def publish_if_absent(self, manifest):
            raise RuntimeError("simulated PostgreSQL publish failure")

    async def run():
        catalog = FailingCatalog()
        store = InMemoryImmutableObjectStore()
        version_id = uuid4()
        publisher = SnapshotPublisher(catalog=catalog, store=store)

        async def publish(payload):
            return await publisher.publish(
                workspace_id=WORKSPACE_A,
                source_id=SOURCE_A,
                owner_id=OWNER,
                version_id=version_id,
                content=payload,
                access_token=TOKEN,
            )

        with pytest.raises(SourceReadError) as error:
            await publish(CONTENT)
        assert error.value.denial.reason is SourceReadDenialReason.VERSION_MISMATCH
        key = snapshot_object_key(WORKSPACE_A, SOURCE_A, version_id)
        assert key in store._objects
        assert await catalog.resolve_version(WORKSPACE_A, SOURCE_A, version_id) is None

        # The orphan stays write-once: a different payload cannot replace it.
        with pytest.raises(SourceReadError) as retry:
            await publish(b"different payload")
        assert retry.value.denial.reason is SourceReadDenialReason.STORAGE_FAILURE
        assert store._objects[key] == CONTENT
        assert await catalog.resolve_version(WORKSPACE_A, SOURCE_A, version_id) is None

    asyncio.run(run())


def test_orphan_object_without_catalog_record_is_not_opened_by_readers():
    path, auth, _, _, _, catalog, _, store, _ = setup_path()
    orphan_version = uuid4()
    orphan_key = snapshot_object_key(WORKSPACE_A, SOURCE_A, orphan_version)
    store._objects[orphan_key] = CONTENT

    assert_denied(
        lambda: read(path, auth, version_id=orphan_version),
        SourceReadDenialReason.VERSION_MISMATCH,
    )
    assert store.opened == []


def test_object_store_contract_exposes_no_overwrite_or_delete_operations():
    from evidence_service.secure_snapshot_read import SnapshotObjectStore

    allowed = {"read_exact", "put_if_absent"}
    for store_type in (
        SnapshotObjectStore,
        InMemoryImmutableObjectStore,
        LocalSupabaseStorageObjectStore,
    ):
        public = {
            name
            for name in dir(store_type)
            if not name.startswith("_") and callable(getattr(store_type, name))
        }
        assert public <= allowed | {"opened", "read_override"}


# --- Fail-closed behavior for dependency, identity and clock failures --------


def _assert_safe_denial(error, reason: SourceReadDenialReason) -> None:
    assert error.value.denial.reason is reason
    rendered = f"{error.value!s} {error.value!r}"
    assert TOKEN not in rendered
    assert CONTENT.decode() not in rendered


def _denied_safely(path, auth, reason: SourceReadDenialReason, **kwargs) -> None:
    with pytest.raises(SourceReadError) as error:
        read(path, auth, **kwargs)
    _assert_safe_denial(error, reason)


def test_storage_failure_withholds_content_and_fails_closed():
    path, auth, _, _, _, _, _, store, _ = setup_path()

    def fail(key, value):
        raise SnapshotObjectUnavailable

    store.read_override = fail
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert len(store.opened) == 1


def test_catalog_failure_denies_before_any_object_open():
    path, auth, _, _, _, catalog, _, store, _ = setup_path()

    async def fail(workspace_id, source_id, version_id):
        raise RuntimeError("catalog unavailable with secret detail")

    catalog.resolve_version = fail
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


def test_acl_failure_denies_before_any_object_open():
    path, auth, _, _, _, _, acl, store, _ = setup_path()

    async def fail(*args):
        raise RuntimeError("acl backend down")

    acl.may_read = fail
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


@pytest.mark.parametrize("truthy", ["yes", 1, object(), [True]])
def test_acl_must_return_exactly_true(truthy):
    path, auth, _, _, _, _, acl, store, _ = setup_path()
    acl.allowed = truthy

    _denied_safely(path, auth, SourceReadDenialReason.ACCESS_DENIED)
    assert store.opened == []


def test_workspace_directory_failure_denies_before_catalog_or_object_access():
    path, auth, _, _, directory, _, _, store, _ = setup_path()

    def boom(call_number):
        raise RuntimeError("membership backend down")

    directory.on_lookup = boom
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


def test_directory_returning_another_workspace_membership_is_rejected():
    path, auth, _, _, directory, _, _, store, _ = setup_path()

    async def other_workspace(principal, access_token, workspace_id):
        return WorkspaceMembership(WORKSPACE_B, "Other workspace", "owner")

    directory.get_membership = other_workspace
    _denied_safely(path, auth, SourceReadDenialReason.REVOKED)
    assert store.opened == []


def test_read_path_independently_rejects_a_resolved_context_for_another_workspace(
    monkeypatch,
):
    # Defence in depth: even if the shared resolver were wrong or replaced, the
    # read path must not accept a context whose workspace differs from the request.
    from evidence_service import secure_snapshot_read
    from evidence_service.tenant_context import WorkspaceContext

    async def wrong_workspace(directory, principal, access_token, workspace_id):
        return WorkspaceContext(WORKSPACE_B, "Other workspace", "owner")

    monkeypatch.setattr(secure_snapshot_read, "resolve_workspace_context", wrong_workspace)
    path, auth, _, _, _, _, _, store, _ = setup_path()

    _denied_safely(path, auth, SourceReadDenialReason.SCOPE_MISMATCH)
    assert store.opened == []


@pytest.mark.parametrize(
    "membership",
    [
        WorkspaceMembership(WORKSPACE_A, "   ", "owner"),
        WorkspaceMembership(WORKSPACE_A, "Local workspace", "superuser"),  # type: ignore[arg-type]
    ],
)
def test_malformed_membership_is_rejected_before_object_access(membership):
    path, auth, _, _, directory, _, _, store, _ = setup_path()

    async def malformed(principal, access_token, workspace_id):
        return membership

    directory.get_membership = malformed
    _denied_safely(path, auth, SourceReadDenialReason.REVOKED)
    assert store.opened == []


def test_identity_service_outage_denies_without_touching_catalog_or_store():
    path, auth, _, verifier, _, _, _, store, _ = setup_path()

    async def outage(token):
        raise AuthenticationUnavailable

    verifier.verify_access_token = outage
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


def test_unexpected_identity_error_denies_without_touching_store():
    path, auth, _, verifier, _, _, _, store, _ = setup_path()

    async def broken(token):
        raise ValueError("unparseable claims containing a secret")

    verifier.verify_access_token = broken
    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


def test_verified_token_with_naive_expiry_is_rejected():
    path, auth, _, verifier, _, _, _, store, _ = setup_path()

    async def naive(token):
        return VerifiedAccessToken(PRINCIPAL, datetime(2030, 1, 1))

    verifier.verify_access_token = naive
    _denied_safely(path, auth, SourceReadDenialReason.SCOPE_MISMATCH)
    assert store.opened == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workspace_id": str(WORKSPACE_A)},
        {"source_id": str(SOURCE_A)},
        {"version_id": str(VERSION_A)},
        {"workspace_id": None},
    ],
)
def test_non_uuid_identifiers_are_rejected_before_any_dependency_call(kwargs):
    path, auth, _, verifier, directory, _, acl, store, _ = setup_path()

    _denied_safely(path, auth, SourceReadDenialReason.SCOPE_MISMATCH, **kwargs)
    assert verifier.calls == 0
    assert directory.calls == []
    assert acl.calls == []
    assert store.opened == []


def test_unauthenticated_request_object_is_rejected_before_any_dependency_call():
    path, _, _, verifier, directory, _, _, store, _ = setup_path()

    with pytest.raises(SourceReadError) as error:
        read(path, object())
    _assert_safe_denial(error, SourceReadDenialReason.SCOPE_MISMATCH)
    assert verifier.calls == 0 and directory.calls == [] and store.opened == []


@pytest.mark.parametrize(
    "bad_clock",
    [
        lambda: datetime(2026, 10, 21, 12, 0),  # naive
        lambda: (_ for _ in ()).throw(RuntimeError("clock failure")),
    ],
)
def test_clock_failure_or_naive_clock_fails_closed(bad_clock):
    path, auth, _, _, _, _, _, store, _ = setup_path()
    path._clock = bad_clock

    _denied_safely(path, auth, SourceReadDenialReason.STORAGE_FAILURE)
    assert store.opened == []


def test_invalid_read_path_configuration_is_refused():
    path, *_ = setup_path()
    for ttl in (timedelta(0), timedelta(seconds=-1), timedelta(minutes=5), 30):
        with pytest.raises(ValueError):
            SecureSnapshotReadPath(
                verifier=path._verifier,
                workspace_directory=path._directory,
                catalog=path._catalog,
                acl=path._acl,
                store=path._store,
                context_ttl=ttl,  # type: ignore[arg-type]
            )
    for limit in (0, -1, True, "8"):
        with pytest.raises(ValueError):
            SecureSnapshotReadPath(
                verifier=path._verifier,
                workspace_directory=path._directory,
                catalog=path._catalog,
                acl=path._acl,
                store=path._store,
                max_snapshot_bytes=limit,  # type: ignore[arg-type]
            )


# --- Digest check isolated from the size check -------------------------------


def test_same_size_tampered_content_is_rejected_by_the_digest_alone():
    path, auth, _, _, _, _, _, store, item = setup_path()
    tampered = b"X" + CONTENT[1:]
    assert len(tampered) == len(CONTENT) and tampered != CONTENT
    store._objects[item.object_key] = tampered

    _denied_safely(path, auth, SourceReadDenialReason.INVALID_SOURCE_DATA)


def test_declared_size_mismatch_with_a_correct_digest_is_rejected():
    from dataclasses import replace

    path, auth, _, _, _, _, _, store, item = setup_path()
    # Digest matches the stored bytes, but the catalog's declared size does not.
    _install(path, replace(item, size_bytes=len(CONTENT) + 1))

    _denied_safely(path, auth, SourceReadDenialReason.INVALID_SOURCE_DATA)
    assert len(store.opened) == 1


def test_publish_rejects_same_size_readback_tampering():
    async def run():
        catalog = InMemorySnapshotCatalog()
        store = InMemoryImmutableObjectStore()
        store.read_override = lambda key, value: b"X" + value[1:]
        publisher = SnapshotPublisher(catalog=catalog, store=store)
        version_id = uuid4()
        with pytest.raises(SourceReadError) as error:
            await publisher.publish(
                workspace_id=WORKSPACE_A,
                source_id=SOURCE_A,
                owner_id=OWNER,
                version_id=version_id,
                content=CONTENT,
                access_token=TOKEN,
            )
        assert error.value.denial.reason is SourceReadDenialReason.INVALID_SOURCE_DATA
        assert await catalog.resolve_version(WORKSPACE_A, SOURCE_A, version_id) is None

    asyncio.run(run())


# --- Storage adapter: bounded streaming and non-2xx handling -----------------

ADAPTER_KEY = snapshot_object_key(WORKSPACE_A, SOURCE_A, VERSION_A)


def _adapter(handler, **kwargs) -> LocalSupabaseStorageObjectStore:
    return LocalSupabaseStorageObjectStore(
        base_url="http://127.0.0.1:55321",
        bucket="source-snapshots",
        publishable_key="sb_publishable_local-test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _read(store) -> bytes:
    return asyncio.run(store.read_exact(ADAPTER_KEY, access_token=TOKEN))


def test_adapter_returns_body_within_the_cap_and_sends_the_user_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["key"] = request.headers["apikey"]
        seen["url"] = str(request.url)
        return httpx.Response(200, content=CONTENT)

    assert _read(_adapter(handler)) == CONTENT
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["key"] == "sb_publishable_local-test-key"
    assert seen["url"].startswith("http://127.0.0.1:55321/storage/v1/object/source-snapshots/")


def test_adapter_rejects_a_body_larger_than_the_cap_without_returning_it():
    def handler(request):
        return httpx.Response(200, content=b"x" * 33)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler, max_read_bytes=32))


def test_adapter_accepts_a_body_exactly_at_the_cap():
    def handler(request):
        return httpx.Response(200, content=b"x" * 32)

    assert _read(_adapter(handler, max_read_bytes=32)) == b"x" * 32


def test_adapter_rejects_declared_oversize_before_reading_the_body():
    stream = _TrackedStream([b"ok"])

    def handler(request):
        return httpx.Response(200, headers={"content-length": "999999"}, stream=stream)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler, max_read_bytes=1024))
    assert stream.yielded == 0, "a rejected Content-Length must prevent reading the body"
    assert stream.closed


def test_adapter_refuses_a_compression_bomb():
    bomb = gzip.compress(b"\0" * 1_000_000)
    assert len(bomb) < 4096

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler, max_read_bytes=4096))


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404, 409, 500, 503])
def test_adapter_treats_redirects_and_errors_as_unavailable(status):
    def handler(request):
        return httpx.Response(status, headers={"location": "http://169.254.169.254/"}, content=CONTENT)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler))


def test_adapter_maps_transport_errors_to_unavailable_without_leaking_detail():
    def handler(request):
        raise httpx.ConnectError("connection refused with token detail")

    with pytest.raises(SnapshotObjectUnavailable) as error:
        _read(_adapter(handler))
    assert TOKEN not in str(error.value)


def test_adapter_refuses_blank_token_before_any_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=CONTENT)

    store = _adapter(handler)
    for token in ("", "   ", None):
        with pytest.raises(SnapshotObjectUnavailable):
            asyncio.run(store.read_exact(ADAPTER_KEY, access_token=token))  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.parametrize("limit", [0, -1, True, "8", 64 * 1024 * 1024 + 1])
def test_adapter_rejects_invalid_read_caps(limit):
    with pytest.raises(ValueError):
        LocalSupabaseStorageObjectStore(
            base_url="http://127.0.0.1:55321",
            bucket="source-snapshots",
            publishable_key="sb_publishable_local-test-key",
            max_read_bytes=limit,  # type: ignore[arg-type]
        )


def test_secure_path_denies_when_the_adapter_reports_an_oversized_object():
    async def run():
        manifest = SourceSnapshotManifest(
            workspace_id=WORKSPACE_A, source_id=SOURCE_A, owner_id=OWNER,
            version_id=VERSION_A, object_key=ADAPTER_KEY,
            sha256="0" * 64, size_bytes=len(CONTENT), state=SnapshotState.PUBLISHED,
            created_at=NOW,
        )
        catalog = InMemorySnapshotCatalog()
        await catalog.publish_if_absent(manifest)
        store = _adapter(
            lambda request: httpx.Response(200, content=b"y" * 64), max_read_bytes=32
        )
        clock = MutableClock()
        directory = MutableWorkspaceDirectory()
        path = SecureSnapshotReadPath(
            verifier=FakeVerifier(clock),
            workspace_directory=directory,
            catalog=catalog,
            acl=MutableAcl(),
            store=store,
            clock=clock,
        )
        with pytest.raises(SourceReadError) as error:
            await path.read(
                AuthenticatedRequest(PRINCIPAL, TOKEN),
                workspace_id=WORKSPACE_A, source_id=SOURCE_A, version_id=VERSION_A,
            )
        _assert_safe_denial(error, SourceReadDenialReason.STORAGE_FAILURE)

    asyncio.run(run())


# --- Adapter hardening: encoding, streaming, cleanup, write semantics --------


class _TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks) -> None:
        self._chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "encoding", ["gzip", "br", "deflate", "zstd", "gzip, identity", " GZIP ", "identity, gzip"]
)
def test_adapter_requests_identity_and_refuses_any_content_encoding(encoding):
    seen = {}
    stream = _TrackedStream([CONTENT])

    def handler(request):
        seen["accept"] = request.headers.get("accept-encoding")
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=stream)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler))
    assert seen["accept"] == "identity"
    assert stream.yielded == 0, "the body must not be read once the encoding is refused"


@pytest.mark.parametrize("headers", [{}, {"content-encoding": "identity"}, {"content-encoding": " Identity "}])
def test_adapter_accepts_an_uncompressed_body(headers):
    def handler(request):
        return httpx.Response(200, headers=headers, content=CONTENT)

    assert _read(_adapter(handler)) == CONTENT


def test_adapter_stops_reading_a_large_stream_at_the_cap():
    # Finite on purpose: if the cap regresses this fails instead of exhausting memory.
    stream = _TrackedStream([b"x" * 8] * 1000)

    def handler(request):
        return httpx.Response(200, stream=stream)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler, max_read_bytes=32))
    assert stream.yielded <= 32 // 8 + 1
    assert stream.closed


@pytest.mark.parametrize(
    "declared",
    [
        "-1", "abc", "1.5", "", "0x10", " 12", "+12",
        "１２",       # fullwidth decimal digits (valid for int(), not ASCII)
        "١٢",         # Arabic-Indic decimal digits
        "²",          # superscript (str.isdigit() is true, int() fails)
        "9" * 5000,   # exceeds Python's integer string conversion limit
    ],
)
def test_adapter_rejects_a_malformed_content_length_without_reading_the_body(declared):
    stream = _TrackedStream([b"ok"])

    def handler(request):
        return httpx.Response(
            200, headers=[(b"content-length", declared.encode("utf-8"))], stream=stream
        )

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_adapter(handler))
    assert stream.yielded == 0
    assert stream.closed


@pytest.mark.parametrize(
    "scenario",
    ["ok", "http500", "oversize", "encoding", "declared-oversize", "transport-error"],
)
def test_adapter_closes_the_response_and_its_owned_client_on_every_outcome(
    monkeypatch, scenario
):
    if scenario == "oversize":
        chunks = [b"x" * 16, b"x" * 16, b"x" * 16]
    else:
        chunks = [CONTENT]
    stream = _TrackedStream(chunks)

    def handler(request):
        if scenario == "transport-error":
            raise httpx.ConnectError("down")
        headers = {}
        if scenario == "encoding":
            headers["content-encoding"] = "gzip"
        if scenario == "declared-oversize":
            headers["content-length"] = "999999"
        return httpx.Response(500 if scenario == "http500" else 200, headers=headers, stream=stream)

    created = []
    real_client = httpx.AsyncClient

    class TrackingClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", TrackingClient)
    store = LocalSupabaseStorageObjectStore(
        base_url="http://127.0.0.1:55321",
        bucket="source-snapshots",
        publishable_key="sb_publishable_local-test-key",
        max_read_bytes=32,
    )

    if scenario == "ok":
        assert _read(store) == CONTENT
    else:
        with pytest.raises(SnapshotObjectUnavailable):
            _read(store)

    assert len(created) == 1 and created[0].is_closed
    if scenario != "transport-error":
        assert stream.closed


def test_adapter_never_closes_a_client_it_does_not_own():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=CONTENT))
    )
    store = LocalSupabaseStorageObjectStore(
        base_url="http://127.0.0.1:55321",
        bucket="source-snapshots",
        publishable_key="sb_publishable_local-test-key",
        client=client,
    )
    assert _read(store) == CONTENT
    assert not client.is_closed
    asyncio.run(client.aclose())


def test_put_if_absent_still_posts_create_only_octet_stream_with_the_user_bearer():
    seen = {}

    def handler(request):
        seen.update(
            method=request.method,
            url=str(request.url),
            auth=request.headers["authorization"],
            apikey=request.headers["apikey"],
            content_type=request.headers["content-type"],
            upsert=request.headers["x-upsert"],
            body=request.content,
        )
        return httpx.Response(200, json={"Key": "ok"})

    asyncio.run(_adapter(handler).put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))

    assert seen["method"] == "POST"
    assert seen["url"] == (
        "http://127.0.0.1:55321/storage/v1/object/source-snapshots/"
        + "/".join(ADAPTER_KEY.split("/"))
    )
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["apikey"] == "sb_publishable_local-test-key"
    assert seen["content_type"] == "application/octet-stream"
    assert seen["upsert"] == "false"
    assert seen["body"] == CONTENT


@pytest.mark.parametrize("status", [409, 412])
def test_put_if_absent_maps_existing_objects_to_conflict(status):
    from evidence_service.secure_snapshot_read import SnapshotObjectConflict

    store = _adapter(lambda request: httpx.Response(status, json={"error": "exists"}))
    with pytest.raises(SnapshotObjectConflict):
        asyncio.run(store.put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 500, 503])
def test_put_if_absent_maps_other_failures_to_unavailable(status):
    store = _adapter(lambda request: httpx.Response(status, json={"error": "no"}))
    with pytest.raises(SnapshotObjectUnavailable):
        asyncio.run(store.put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))


def test_put_if_absent_maps_transport_errors_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(SnapshotObjectUnavailable):
        asyncio.run(_adapter(handler).put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))


@pytest.mark.parametrize(
    "bad_key", ["", "/abs/key", "a//b", "a/../b", "./a", "a/./b", "a\\b", "../../outside"]
)
def test_invalid_object_keys_raise_value_error_before_any_request(bad_key):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=CONTENT)

    store = _adapter(handler)
    with pytest.raises(ValueError):
        asyncio.run(store.read_exact(bad_key, access_token=TOKEN))
    with pytest.raises(ValueError):
        asyncio.run(store.put_if_absent(bad_key, CONTENT, access_token=TOKEN))
    assert calls == []


def _path_with_adapter(
    adapter, *, content: bytes, declared_size: int, path_max: int, **path_kwargs
):
    import hashlib

    async def build():
        manifest = SourceSnapshotManifest(
            workspace_id=WORKSPACE_A, source_id=SOURCE_A, owner_id=OWNER,
            version_id=VERSION_A, object_key=ADAPTER_KEY,
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=declared_size,
            state=SnapshotState.PUBLISHED, created_at=NOW,
        )
        catalog = InMemorySnapshotCatalog()
        await catalog.publish_if_absent(manifest)
        return catalog

    catalog = asyncio.run(build())
    clock = MutableClock()
    return SecureSnapshotReadPath(
        verifier=FakeVerifier(clock),
        workspace_directory=MutableWorkspaceDirectory(),
        catalog=catalog,
        acl=MutableAcl(),
        store=adapter,
        clock=clock,
        max_snapshot_bytes=path_max,
        **path_kwargs,
    )


def test_adapter_cap_below_the_path_limit_still_fails_closed_for_valid_bytes():
    content = b"z" * 64
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=content)

    path = _path_with_adapter(
        _adapter(handler, max_read_bytes=32), content=content, declared_size=64, path_max=1024
    )
    _denied_safely(path, AuthenticatedRequest(PRINCIPAL, TOKEN), SourceReadDenialReason.STORAGE_FAILURE)
    assert len(calls) >= 1


def test_path_limit_below_the_adapter_cap_rejects_the_manifest_before_storage_is_opened():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=CONTENT)

    path = _path_with_adapter(
        _adapter(handler), content=CONTENT, declared_size=len(CONTENT), path_max=len(CONTENT) - 1
    )
    _denied_safely(path, AuthenticatedRequest(PRINCIPAL, TOKEN), SourceReadDenialReason.INVALID_SOURCE_DATA)
    assert calls == []


# --- Overall deadlines, trust_env and pre-open revalidation -----------------

_GUARD = 2.0  # a regression must fail the test instead of hanging it


def _guarded(coro):
    async def run():
        return await asyncio.wait_for(coro, _GUARD)

    return asyncio.run(run())


def _read_guarded(path, auth):
    return _guarded(
        path.read(auth, workspace_id=WORKSPACE_A, source_id=SOURCE_A, version_id=VERSION_A)
    )


def _never():
    async def hang(*_a, **_k):
        await asyncio.Event().wait()

    return hang


def _fast_path(**kwargs):
    parts = setup_path()
    path, auth, clock, verifier, directory, catalog, acl, store, item = parts
    path = SecureSnapshotReadPath(
        verifier=verifier, workspace_directory=directory, catalog=catalog,
        acl=acl, store=store, clock=clock, max_read_seconds=0.1, **kwargs,
    )
    return path, auth, verifier, directory, catalog, acl, store


@pytest.mark.parametrize("hung", ["verifier", "directory", "catalog", "acl", "store"])
def test_a_hanging_dependency_is_cut_off_and_nothing_is_delivered(hung):
    path, auth, verifier, directory, catalog, acl, store = _fast_path()
    if hung == "verifier":
        verifier.verify_access_token = _never()
    elif hung == "directory":
        directory.get_membership = _never()
    elif hung == "catalog":
        catalog.resolve_version = _never()
    elif hung == "acl":
        acl.may_read = _never()
    else:
        store.read_exact = _never()
    with pytest.raises(SourceReadError) as error:
        _read_guarded(path, auth)
    _assert_safe_denial(error, SourceReadDenialReason.STORAGE_FAILURE)


def test_a_dependency_that_hangs_only_at_delivery_time_is_cut_off():
    path, auth, verifier, directory, catalog, acl, store = _fast_path()
    original = acl.may_read

    async def hang_on_third(*args):
        if len(acl.calls) >= 2:
            await asyncio.Event().wait()
        return await original(*args)

    acl.may_read = hang_on_third
    with pytest.raises(SourceReadError) as error:
        _read_guarded(path, auth)
    assert error.value.denial.reason is SourceReadDenialReason.STORAGE_FAILURE


def test_deadline_is_bounded_by_the_remaining_read_context_lifetime():
    import time

    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    parts = setup_path()
    _, auth, _, verifier, directory, catalog, acl, store, _ = parts
    verifier.clock = MutableClock()  # lifetime irrelevant; context ttl governs
    path = SecureSnapshotReadPath(
        verifier=verifier, workspace_directory=directory, catalog=catalog,
        acl=acl, store=store, clock=clock,
        context_ttl=timedelta(milliseconds=300), max_read_seconds=5.0,
    )
    verifier.clock.value = datetime.now(timezone.utc)
    store.read_exact = _never()
    started = time.monotonic()
    with pytest.raises(SourceReadError) as error:
        _read_guarded(path, auth)
    assert time.monotonic() - started < _GUARD
    assert error.value.denial.reason in {
        SourceReadDenialReason.STORAGE_FAILURE,
        SourceReadDenialReason.EXPIRED,
    }


def test_slow_stream_through_the_path_is_cut_off_and_closed_without_delivery():
    class Slow(httpx.AsyncByteStream):
        def __init__(self):
            self.yielded = 0
            self.closed = False

        async def __aiter__(self):
            for _ in range(50):
                self.yielded += 1
                yield b"x"
                await asyncio.sleep(0.05)

        async def aclose(self):
            self.closed = True

    stream = Slow()
    adapter = _adapter(lambda r: httpx.Response(200, stream=stream))
    path = _path_with_adapter(
        adapter, content=b"x" * 50, declared_size=50, path_max=1024, max_read_seconds=0.15
    )
    auth = AuthenticatedRequest(PRINCIPAL, TOKEN)
    with pytest.raises(SourceReadError) as error:
        _read_guarded(path, auth)
    assert error.value.denial.reason is SourceReadDenialReason.STORAGE_FAILURE
    assert stream.closed
    assert stream.yielded < 50


def test_adapter_total_timeout_cuts_a_slow_stream_by_itself():
    class Slow(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for _ in range(50):
                yield b"x"
                await asyncio.sleep(0.05)

        async def aclose(self):
            Slow.closed = True

    adapter = _adapter(
        lambda r: httpx.Response(200, stream=Slow()), total_timeout_seconds=0.15
    )
    with pytest.raises(SnapshotObjectUnavailable):
        _guarded(adapter.read_exact(ADAPTER_KEY, access_token=TOKEN))
    assert Slow.closed


def test_adapter_total_timeout_applies_to_a_hanging_response_and_post():
    async def handler(request):
        await asyncio.Event().wait()

    adapter = _adapter(handler, total_timeout_seconds=0.1)
    with pytest.raises(SnapshotObjectUnavailable):
        _guarded(adapter.read_exact(ADAPTER_KEY, access_token=TOKEN))
    with pytest.raises(SnapshotObjectUnavailable):
        _guarded(adapter.put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))


@pytest.mark.parametrize("bad", [0, -1, 61, True, float("nan"), float("inf"), "5", None])
def test_deadline_configuration_is_validated(bad):
    with pytest.raises(ValueError):
        LocalSupabaseStorageObjectStore(
            base_url="http://127.0.0.1:55321", bucket="source-snapshots",
            publishable_key="sb_publishable_local-test-key", total_timeout_seconds=bad,
        )
    parts = setup_path()
    path = parts[0]
    with pytest.raises(ValueError):
        SecureSnapshotReadPath(
            verifier=path._verifier, workspace_directory=path._directory,
            catalog=path._catalog, acl=path._acl, store=path._store,
            clock=path._clock, max_read_seconds=bad,
        )


def test_owned_clients_ignore_proxy_environment(monkeypatch):
    created = []
    real = httpx.AsyncClient

    class Capturing(real):
        def __init__(self, *args, **kwargs):
            created.append(kwargs)
            super().__init__(*args, transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=CONTENT)), **{
                k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    store = LocalSupabaseStorageObjectStore(
        base_url="http://127.0.0.1:55321", bucket="source-snapshots",
        publishable_key="sb_publishable_local-test-key",
    )
    asyncio.run(store.read_exact(ADAPTER_KEY, access_token=TOKEN))
    try:
        asyncio.run(store.put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN))
    except SnapshotObjectUnavailable:
        pass
    assert len(created) == 2
    assert all(kwargs.get("trust_env") is False for kwargs in created)


def test_revoked_membership_after_the_first_check_prevents_opening_storage():
    path, auth, _, _, directory, _, acl, store, _ = setup_path()

    def revoke_on_second(call):
        if call == 2:
            directory.active.clear()

    directory.on_lookup = revoke_on_second
    assert_denied(lambda: read(path, auth), SourceReadDenialReason.REVOKED)
    assert store.opened == []
    assert len(acl.calls) == 1


def test_withdrawn_grant_after_the_first_check_prevents_opening_storage():
    path, auth, _, _, directory, _, acl, store, _ = setup_path()
    acl.deny_after = 2
    assert_denied(lambda: read(path, auth), SourceReadDenialReason.ACCESS_DENIED)
    assert store.opened == []
    assert len(directory.calls) == 2


def test_injected_client_is_used_as_given_and_its_settings_are_not_replaced(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=CONTENT)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, trust_env=True, follow_redirects=True,
        timeout=httpx.Timeout(7.0), headers={"x-caller-setting": "kept"},
    )
    constructed = []
    real = httpx.AsyncClient

    class Spy(real):
        def __init__(self, *args, **kwargs):
            constructed.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Spy)
    before = (client.follow_redirects, client.timeout, client._transport, client._trust_env)
    store = LocalSupabaseStorageObjectStore(
        base_url="http://127.0.0.1:55321", bucket="source-snapshots",
        publishable_key="sb_publishable_local-test-key", client=client,
    )

    async def run():
        assert await store.read_exact(ADAPTER_KEY, access_token=TOKEN) == CONTENT
        await store.put_if_absent(ADAPTER_KEY, CONTENT, access_token=TOKEN)
        return client.is_closed

    closed = asyncio.run(run())
    assert len(seen) == 2, "the injected transport must carry both requests"
    assert constructed == [], "no replacement client may be created"
    assert not closed, "the adapter must not close a client it does not own"
    assert before == (client.follow_redirects, client.timeout, client._transport, client._trust_env)
    assert client._trust_env is True and client.follow_redirects is True
    assert client.headers["x-caller-setting"] == "kept"
    assert all(r.headers["x-caller-setting"] == "kept" for r in seen)
