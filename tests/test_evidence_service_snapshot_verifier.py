import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from evidence_service.snapshot_verifier import (
    LocalSupabaseVerificationGateway,
    SnapshotVerificationError,
    SnapshotVerificationService,
    StagedSnapshotClaim,
    VerificationFailure,
    snapshot_object_key,
)

WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE = UUID("b3000000-0000-4000-8000-000000000003")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
OBJECT_ID = UUID("f1000000-0000-4000-8000-000000000001")
OBJECT_VERSION = "5f0c6a7e-3d62-4c3c-9a43-000000000001"
TOKEN = "synthetic-verifier-user-token"
CONTENT = b"verified snapshot bytes\n"
KEY = snapshot_object_key(WORKSPACE, SOURCE, VERSION)
GUARD = 2.0


def _jwt(role: str) -> str:
    def b64(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'role': role})}.signature"


def _claim(content: bytes = CONTENT, **override) -> StagedSnapshotClaim:
    values = dict(
        workspace_id=WORKSPACE, source_id=SOURCE, version_id=VERSION, object_key=KEY,
        claimed_sha256=hashlib.sha256(content).hexdigest(), claimed_size_bytes=len(content),
        storage_object_id=OBJECT_ID, storage_object_version=OBJECT_VERSION,
    )
    values.update(override)
    return StagedSnapshotClaim(**values)


class FakeGateway:
    def __init__(self, claim=None) -> None:
        self.claim = claim if claim is not None else _claim()
        self.begin_calls = 0
        self.published: list[tuple] = []
        self.begin_error: Exception | None = None
        self.publish_error: Exception | None = None
        self.begin_hang = False
        self.publish_hang = False

    async def begin(self, access_token, workspace_id, source_id, version_id):
        self.begin_calls += 1
        if self.begin_hang:
            await asyncio.Event().wait()
        if self.begin_error:
            raise self.begin_error
        return self.claim

    async def publish(self, access_token, claim, *, sha256, size_bytes):
        if self.publish_hang:
            await asyncio.Event().wait()
        if self.publish_error:
            raise self.publish_error
        if self.published:
            raise RuntimeError("already published")
        self.published.append((claim, sha256, size_bytes))


class FakeReader:
    def __init__(self, content=CONTENT) -> None:
        self.content = content
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.hang = False

    async def read_exact(self, object_key, *, access_token):
        self.calls.append(object_key)
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.content


def _service(gateway=None, reader=None, **kwargs):
    gateway = gateway or FakeGateway()
    reader = reader or FakeReader()
    return SnapshotVerificationService(gateway=gateway, reader=reader, **kwargs), gateway, reader


def _verify(service, token=TOKEN, **ids):
    args = dict(workspace_id=WORKSPACE, source_id=SOURCE, version_id=VERSION)
    args.update(ids)

    async def run():
        return await asyncio.wait_for(
            service.verify_and_publish(token, **args), GUARD
        )

    return asyncio.run(run())


def _fails(service, reason: VerificationFailure, **kwargs):
    with pytest.raises(SnapshotVerificationError) as error:
        _verify(service, **kwargs)
    assert error.value.reason is reason
    rendered = f"{error.value!s} {error.value!r}"
    for secret in (TOKEN, KEY, CONTENT.decode().strip(), OBJECT_ID.hex):
        assert secret not in rendered
    assert error.value.__cause__ is None
    return error.value


# --- Service: integrity ------------------------------------------------------


def test_publishes_the_digest_recomputed_from_the_bytes_that_were_read():
    service, gateway, reader = _service()
    result = _verify(service)
    assert result.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert result.size_bytes == len(CONTENT)
    (claim, sha256, size), = gateway.published
    assert (sha256, size) == (result.sha256, len(CONTENT))
    assert reader.calls == [KEY]
    # The object identity examined at begin() is the one presented at publish().
    assert (claim.storage_object_id, claim.storage_object_version) == (OBJECT_ID, OBJECT_VERSION)


def test_a_single_flipped_byte_is_rejected_and_nothing_is_published():
    tampered = bytearray(CONTENT)
    tampered[0] ^= 0x01
    service, gateway, _ = _service(reader=FakeReader(bytes(tampered)))
    _fails(service, VerificationFailure.DIGEST_MISMATCH)
    assert gateway.published == []


@pytest.mark.parametrize("content", [CONTENT[:-1], CONTENT + b"x", b""])
def test_size_mismatch_is_rejected_before_publication(content):
    service, gateway, _ = _service(reader=FakeReader(content))
    _fails(service, VerificationFailure.SIZE_MISMATCH)
    assert gateway.published == []


def test_a_claim_larger_than_the_limit_is_rejected_without_reading_storage():
    gateway = FakeGateway(_claim(claimed_size_bytes=2048))
    service, gateway, reader = _service(gateway=gateway, max_snapshot_bytes=1024)
    _fails(service, VerificationFailure.CLAIM_INVALID)
    assert reader.calls == []


def test_bytes_larger_than_the_limit_are_never_published_even_if_the_claim_agrees():
    big = b"z" * 64
    service, gateway, _ = _service(
        gateway=FakeGateway(_claim(big)), reader=FakeReader(big), max_snapshot_bytes=32
    )
    _fails(service, VerificationFailure.CLAIM_INVALID)
    assert gateway.published == []


@pytest.mark.parametrize(
    "override",
    [
        {"workspace_id": OTHER_WORKSPACE},
        {"source_id": UUID("c2000000-0000-4000-8000-000000000002")},
        {"version_id": UUID("d2000000-0000-4000-8000-000000000002")},
        {"object_key": "workspace/other/source/x/version/y"},
        {"object_key": "../" + KEY},
        {"claimed_sha256": "A" * 64},
        {"claimed_sha256": "z" * 64},
        {"claimed_sha256": "0" * 63},
        {"claimed_size_bytes": True},
        {"claimed_size_bytes": -1},
        {"claimed_size_bytes": "24"},
        {"storage_object_id": "f1000000-0000-4000-8000-000000000001"},
        {"storage_object_version": ""},
        {"storage_object_version": "v" * 513},
        {"storage_object_version": None},
    ],
)
def test_an_inconsistent_claim_fails_closed_before_any_storage_read(override):
    service, gateway, reader = _service(gateway=FakeGateway(_claim(**override)))
    _fails(service, VerificationFailure.CLAIM_INVALID)
    assert reader.calls == [] and gateway.published == []


def test_a_claim_of_the_wrong_type_is_rejected():
    service, _, reader = _service(gateway=FakeGateway(claim={"claimed_sha256": "0" * 64}))
    _fails(service, VerificationFailure.CLAIM_INVALID)
    assert reader.calls == []


def test_claim_digest_is_not_trusted_over_the_bytes():
    service, gateway, _ = _service(gateway=FakeGateway(_claim(b"different content")))
    _fails(service, VerificationFailure.SIZE_MISMATCH)
    assert gateway.published == []


# --- Service: authorisation and failures --------------------------------------


def test_a_caller_that_is_not_authorised_stops_before_any_storage_read():
    gateway = FakeGateway()
    gateway.begin_error = SnapshotVerificationError(VerificationFailure.NOT_AUTHORISED)
    service, _, reader = _service(gateway=gateway)
    _fails(service, VerificationFailure.NOT_AUTHORISED)
    assert reader.calls == []


def test_a_snapshot_that_is_not_staged_fails_closed():
    gateway = FakeGateway()
    gateway.begin_error = RuntimeError(f"row not found {KEY} {TOKEN}")
    service, _, reader = _service(gateway=gateway)
    _fails(service, VerificationFailure.NOT_STAGED)
    assert reader.calls == []


@pytest.mark.parametrize(
    "failure", [RuntimeError(f"boom {KEY}"), OSError("x")]
)
def test_storage_failures_fail_closed_without_publishing(failure):
    reader = FakeReader()
    reader.error = failure
    service, gateway, _ = _service(reader=reader)
    _fails(service, VerificationFailure.STORAGE_UNAVAILABLE)
    assert gateway.published == []


def test_a_non_bytes_storage_result_is_rejected():
    service, gateway, _ = _service(reader=FakeReader("text"))
    _fails(service, VerificationFailure.STORAGE_UNAVAILABLE)
    assert gateway.published == []


def test_a_rejected_publication_is_reported_without_leaking_details():
    gateway = FakeGateway()
    gateway.publish_error = RuntimeError(f"db said no about {KEY}")
    service, _, _ = _service(gateway=gateway)
    _fails(service, VerificationFailure.PUBLISH_REJECTED)


def test_two_concurrent_verifications_publish_at_most_once():
    service, gateway, _ = _service()

    async def run():
        return await asyncio.wait_for(
            asyncio.gather(
                service.verify_and_publish(TOKEN, workspace_id=WORKSPACE, source_id=SOURCE, version_id=VERSION),
                service.verify_and_publish(TOKEN, workspace_id=WORKSPACE, source_id=SOURCE, version_id=VERSION),
                return_exceptions=True,
            ),
            GUARD,
        )

    results = asyncio.run(run())
    assert len(gateway.published) == 1
    failures = [r for r in results if isinstance(r, SnapshotVerificationError)]
    assert len(failures) == 1 and failures[0].reason is VerificationFailure.PUBLISH_REJECTED


@pytest.mark.parametrize(
    "token",
    ["", "   ", " padded", "tail ", "a\nb", "a\rb", None, 5, b"bytes", _jwt("service_role")],
)
def test_invalid_tokens_and_service_role_tokens_are_refused_before_any_call(token):
    service, gateway, reader = _service()
    _fails(service, VerificationFailure.INVALID_REQUEST, token=token)
    assert gateway.begin_calls == 0 and reader.calls == []


def test_a_non_service_role_jwt_is_not_refused_by_the_client_side_guard():
    service, gateway, _ = _service()
    _verify(service, token=_jwt("authenticated"))
    assert len(gateway.published) == 1


@pytest.mark.parametrize(
    "ids",
    [
        {"workspace_id": str(WORKSPACE)},
        {"source_id": None},
        {"version_id": 7},
    ],
)
def test_non_uuid_identifiers_are_refused(ids):
    service, gateway, _ = _service()
    _fails(service, VerificationFailure.INVALID_REQUEST, **ids)
    assert gateway.begin_calls == 0


@pytest.mark.parametrize("stage", ["begin", "read", "publish"])
def test_each_stage_is_cut_off_by_the_overall_deadline(stage):
    service, gateway, reader = _service(max_verify_seconds=0.1)
    if stage == "begin":
        gateway.begin_hang = True
    elif stage == "read":
        reader.hang = True
    else:
        gateway.publish_hang = True
    _fails(service, VerificationFailure.TIMEOUT)
    assert gateway.published == []


@pytest.mark.parametrize("bad", [0, -1, 121, True, float("nan"), float("inf"), "5", None])
def test_service_configuration_is_validated(bad):
    with pytest.raises(ValueError):
        SnapshotVerificationService(gateway=FakeGateway(), reader=FakeReader(), max_verify_seconds=bad)


@pytest.mark.parametrize("bad", [0, -1, True, "5", None, 1.5])
def test_service_byte_limit_is_validated(bad):
    with pytest.raises(ValueError):
        SnapshotVerificationService(gateway=FakeGateway(), reader=FakeReader(), max_snapshot_bytes=bad)


# --- Gateway (MockTransport; not a live service) -----------------------------

API_KEY = "sb_publishable_local-test-key"


def _gateway(handler, **kwargs):
    return LocalSupabaseVerificationGateway(
        base_url="http://127.0.0.1:55321",
        publishable_key=API_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _row(**override):
    row = {
        "out_object_key": KEY,
        "out_claimed_sha256": hashlib.sha256(CONTENT).hexdigest(),
        "out_claimed_size_bytes": len(CONTENT),
        "out_storage_object_id": str(OBJECT_ID),
        "out_storage_object_version": OBJECT_VERSION,
    }
    row.update(override)
    return row


def _begin(gateway, token=TOKEN):
    return asyncio.run(
        asyncio.wait_for(gateway.begin(token, WORKSPACE, SOURCE, VERSION), GUARD)
    )


def _failure_of(operation) -> VerificationFailure:
    with pytest.raises(SnapshotVerificationError) as error:
        operation()
    return error.value.reason


def test_gateway_begin_sends_a_user_scoped_identity_encoded_rpc_and_parses_the_claim():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[_row()])

    claim = _begin(_gateway(handler))
    assert claim == _claim()
    request, = seen
    assert request.method == "POST"
    assert request.url.path == "/rest/v1/rpc/begin_snapshot_verification"
    assert request.headers["apikey"] == API_KEY
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert request.headers["accept-encoding"] == "identity"
    assert json.loads(request.content) == {
        "p_workspace_id": str(WORKSPACE), "p_source_id": str(SOURCE), "p_version_id": str(VERSION),
    }


@pytest.mark.parametrize("status", [401, 403])
def test_gateway_maps_authorisation_failures(status):
    reason = _failure_of(lambda: _begin(_gateway(lambda r: httpx.Response(status, json={}))))
    assert reason is VerificationFailure.NOT_AUTHORISED


@pytest.mark.parametrize("status", [400, 404, 500])
def test_gateway_treats_other_begin_errors_as_not_staged(status):
    reason = _failure_of(lambda: _begin(_gateway(lambda r: httpx.Response(status, json={}))))
    assert reason is VerificationFailure.NOT_STAGED


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [_row(), _row()],
        {"out_object_key": KEY},
        [{"out_object_key": KEY}],
        [_row(out_storage_object_id="not-a-uuid")],
        ["text"],
    ],
)
def test_gateway_rejects_unexpected_begin_payloads(payload):
    reason = _failure_of(lambda: _begin(_gateway(lambda r: httpx.Response(200, json=payload))))
    assert reason is VerificationFailure.CLAIM_INVALID


def test_gateway_rejects_non_json_and_compressed_responses():
    assert _failure_of(
        lambda: _begin(_gateway(lambda r: httpx.Response(200, content=b"<html>")))
    ) is VerificationFailure.CLAIM_INVALID
    gzip_stream = _Stream([json.dumps([_row()]).encode()])
    assert _failure_of(
        lambda: _begin(
            _gateway(lambda r: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=gzip_stream))
        )
    ) is VerificationFailure.CLAIM_INVALID
    assert gzip_stream.yielded == 0 and gzip_stream.closed


class _Stream(httpx.AsyncByteStream):
    def __init__(self, chunks) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def test_gateway_caps_the_response_and_closes_it():
    stream = _Stream([b"x" * 40_000] * 4)
    reason = _failure_of(lambda: _begin(_gateway(lambda r: httpx.Response(200, stream=stream))))
    assert reason is VerificationFailure.CLAIM_INVALID
    assert stream.closed and stream.yielded < 4


def test_gateway_total_timeout_cuts_a_hanging_service():
    async def handler(request):
        await asyncio.Event().wait()

    reason = _failure_of(lambda: _begin(_gateway(handler, total_timeout_seconds=0.1)))
    assert reason is VerificationFailure.STORAGE_UNAVAILABLE


def test_gateway_publish_sends_the_evidence_and_maps_rejection():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(204)

    gateway = _gateway(handler)
    asyncio.run(
        asyncio.wait_for(
            gateway.publish(TOKEN, _claim(), sha256=hashlib.sha256(CONTENT).hexdigest(), size_bytes=len(CONTENT)),
            GUARD,
        )
    )
    assert seen == [{
        "p_workspace_id": str(WORKSPACE), "p_source_id": str(SOURCE), "p_version_id": str(VERSION),
        "p_sha256": hashlib.sha256(CONTENT).hexdigest(), "p_size_bytes": len(CONTENT),
        "p_storage_object_id": str(OBJECT_ID), "p_storage_object_version": OBJECT_VERSION,
    }]

    for status, expected in [(403, VerificationFailure.NOT_AUTHORISED), (400, VerificationFailure.PUBLISH_REJECTED)]:
        rejecting = _gateway(lambda r, s=status: httpx.Response(s, json={}))
        assert _failure_of(
            lambda: asyncio.run(rejecting.publish(TOKEN, _claim(), sha256="0" * 64, size_bytes=1))
        ) is expected


def test_gateway_refuses_a_service_role_token_without_sending_anything():
    calls = []
    gateway = _gateway(lambda r: calls.append(r) or httpx.Response(200, json=[_row()]))
    assert _failure_of(lambda: _begin(gateway, token=_jwt("service_role"))) is VerificationFailure.INVALID_REQUEST
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "https://127.0.0.1:55321"},
        {"base_url": "http://example.com"},
        {"base_url": "http://127.0.0.1:55321/path"},
        {"base_url": "http://user:pw@127.0.0.1:55321"},
        {"publishable_key": _jwt("service_role")},
        {"publishable_key": ""},
        {"timeout_seconds": 0},
        {"total_timeout_seconds": 500},
    ],
)
def test_gateway_configuration_is_validated(kwargs):
    base = {"base_url": "http://127.0.0.1:55321", "publishable_key": API_KEY}
    base.update(kwargs)
    with pytest.raises(ValueError):
        LocalSupabaseVerificationGateway(**base)


def test_gateway_owned_client_ignores_proxy_environment(monkeypatch):
    created = []
    real = httpx.AsyncClient

    class Capturing(real):
        def __init__(self, *args, **kwargs):
            created.append(kwargs)
            kwargs = {k: v for k, v in kwargs.items() if k != "transport"}
            super().__init__(*args, transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=[_row()])), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    gateway = LocalSupabaseVerificationGateway(
        base_url="http://127.0.0.1:55321", publishable_key=API_KEY
    )
    _begin(gateway)
    assert len(created) == 1 and created[0].get("trust_env") is False


def test_gateway_uses_an_injected_client_as_given(monkeypatch):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[_row()])),
        trust_env=True, follow_redirects=True,
    )
    constructed = []
    real = httpx.AsyncClient

    class Spy(real):
        def __init__(self, *a, **k):
            constructed.append(k)
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", Spy)
    gateway = LocalSupabaseVerificationGateway(
        base_url="http://127.0.0.1:55321", publishable_key=API_KEY, client=client
    )
    _begin(gateway)
    assert constructed == [] and not client.is_closed
    assert client._trust_env is True and client.follow_redirects is True


# --- Migration text checks (static; NOT a substitute for pgTAP) --------------

MIGRATION = "\n".join(
    line
    for line in (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261022120000_snapshot_verifier_identity.sql"
    )
    .read_text()
    .splitlines()
    if not line.lstrip().startswith("--")
)
CATALOG_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20261022100000_source_snapshot_catalog.sql"
).read_text()


def _function_sql(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\$function\$;", MIGRATION, re.S
    )
    assert match, name
    return match.group(0)


@pytest.mark.parametrize(
    "name",
    ["verifier_may_read_staged_object", "begin_snapshot_verification", "publish_verified_snapshot"],
)
def test_migration_functions_are_definer_with_an_empty_search_path(name):
    body = _function_sql(name)
    assert "security definer" in body
    assert "set search_path = ''" in body
    assert "snapshot_verifier_allowlist" in body


def test_migration_checks_the_allowlist_in_both_rpcs():
    for name in ("begin_snapshot_verification", "publish_verified_snapshot"):
        assert "allowed.user_id = private.verified_user_id()" in _function_sql(name)


def test_verifier_refuses_snapshots_outside_the_staged_state():
    begin = _function_sql("begin_snapshot_verification")
    publish = _function_sql("publish_verified_snapshot")
    assert re.search(r"snapshot\.state\s*=\s*'staged'", begin)
    assert re.search(r"snapshot\.state\s*=\s*'staged'", publish)
    assert "no verifiable staged snapshot" in begin
    assert "no verifiable staged snapshot" in publish


def test_public_verifier_rpcs_grant_execute_only_to_authenticated():
    public_grants = re.findall(
        r"grant\s+execute\s+on\s+function\s+public\.[^;]*?\s+to\s+([^;]+);",
        MIGRATION,
        re.I | re.S,
    )
    assert len(public_grants) == 3
    assert all(grant.strip() == "authenticated" for grant in public_grants)
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+private\.verified_user_id\(\)\s+to\s+snapshot_publisher_owner;",
        MIGRATION,
        re.I,
    )
    assert not re.search(r"grant[^;]*\bto\b[^;]*\b(anon|service_role|public)\b", MIGRATION, re.I)
    assert "revoke all on table private.snapshot_verifier_allowlist" in MIGRATION


def test_migration_never_grants_write_on_the_allowlist_or_membership_to_any_role():
    assert not re.search(
        r"grant\s+(insert|update|delete|all)[^;]*snapshot_verifier_allowlist", MIGRATION, re.I
    )
    assert not re.search(r"grant\s+snapshot_(publisher_owner|verifier)\s+to\s+(anon|authenticated|service_role)", MIGRATION, re.I)


def test_migration_binds_publication_to_the_storage_object_identity():
    trigger = re.search(
        r"function private\.require_verification_binding\(\).*?\$function\$;", MIGRATION, re.S
    ).group(0)
    for needle in ("stored.id = new.storage_object_id", "stored.version = new.storage_object_version", "(stored.metadata ->> 'size')::bigint = new.size_bytes"):
        assert needle in trigger
    assert "create trigger source_snapshots_require_verification_binding" in MIGRATION


def test_migration_does_not_lock_storage_objects_or_grant_update_on_them():
    assert "for share" not in MIGRATION.lower()
    assert not re.search(r"grant\s+update[^;]*storage\.objects", MIGRATION, re.I)


_RPC_NAMES = ["begin_snapshot_verification", "publish_verified_snapshot", "verifier_may_read_staged_object"]


@pytest.mark.parametrize("name", _RPC_NAMES)
def test_migration_functions_touch_only_the_three_expected_relations(name):
    body = _function_sql(name)
    relations = set(re.findall(r"\b(?:from|join|update|into)\s+([a-z_]+\.[a-z_]+)", body))
    assert relations <= {
        "private.snapshot_verifier_allowlist",
        "public.source_snapshots",
        "storage.objects",
    }, relations


@pytest.mark.parametrize("name", _RPC_NAMES)
def test_migration_functions_contain_no_ddl_dynamic_sql_or_role_changes(name):
    body = _function_sql(name).lower()
    for forbidden in (
        "execute ", "grant ", "revoke ", "alter ", "create ", "drop ", "truncate",
        "set role", "set local", "reset role", "session_user", "set_config",
        "insert into", "delete from", "copy ",
    ):
        assert forbidden not in body.split("as $function$", 1)[1], forbidden


def test_publish_function_changes_only_the_publication_columns():
    body = _function_sql("publish_verified_snapshot")
    update = re.search(r"update public\.source_snapshots as snapshot\s+set(.*?)where", body, re.S).group(1)
    assigned = set(re.findall(r"(\w+)\s*=", update))
    assert assigned == {
        "state", "verified_sha256", "verified_size_bytes",
        "verified_by", "storage_object_id", "storage_object_version",
    }
    assert "verified_by = private.verified_user_id()" in update


def test_migration_never_seeds_the_allowlist_so_publication_is_closed_by_default():
    assert not re.search(r"insert\s+into\s+private\.snapshot_verifier_allowlist", MIGRATION, re.I)
    assert not re.search(r"\bcopy\b[^;]*snapshot_verifier_allowlist", MIGRATION, re.I)


def test_migration_keeps_the_legacy_role_inert_and_rejects_preexisting_acl_dependencies():
    assert "select private.assert_snapshot_role_safe('snapshot_verifier')" in MIGRATION
    assert "dependency.deptype in ('o', 'a')" in CATALOG_MIGRATION
    assert not re.search(r"grant\s+update[^;]*to\s+snapshot_verifier", MIGRATION, re.I)


def test_migration_assigns_all_three_functions_to_the_dedicated_nologin_owner():
    for signature in (
        r"verifier_may_read_staged_object\(text\)",
        r"begin_snapshot_verification\(uuid, uuid, uuid\)",
        r"publish_verified_snapshot\(uuid, uuid, uuid, text, bigint, uuid, text\)",
    ):
        assert re.search(rf"alter function public\.{signature}\s+owner to snapshot_publisher_owner;", MIGRATION)
    role_creation = re.search(
        r"create\s+role\s+snapshot_publisher_owner\s+(.*?);", MIGRATION, re.I | re.S
    )
    assert role_creation
    for option in (
        "nologin", "noinherit", "nobypassrls", "nosuperuser",
        "nocreatedb", "nocreaterole", "noreplication",
    ):
        assert option in role_creation.group(1).lower()
    assert not re.search(r"owner to (postgres|supabase_admin|service_role|authenticated|anon)", MIGRATION)


# --- Workspace scope of the verifier identity (static checks of the SQL text) --


def test_allowlist_entries_are_scoped_to_a_workspace_and_cannot_be_unscoped():
    table = re.search(
        r"create table if not exists private\.snapshot_verifier_allowlist \((.*?)\n\);", MIGRATION, re.S
    ).group(1)
    assert re.search(r"workspace_id uuid not null references public\.workspaces \(id\)", table)
    assert re.search(r"primary key \(user_id, workspace_id\)", table)
    assert re.search(r"user_id uuid not null", table)


@pytest.mark.parametrize("name", ["begin_snapshot_verification", "publish_verified_snapshot"])
def test_rpcs_check_the_requested_workspace_against_the_callers_scope_before_any_lookup(name):
    body = _function_sql(name)
    scope_check = (
        "allowed.user_id = private.verified_user_id()\n          and allowed.workspace_id = p_workspace_id"
    )
    assert scope_check in body
    first_check = body.index(scope_check)
    for relation in ("public.source_snapshots", "storage.objects"):
        for match in re.finditer(re.escape(relation), body):
            # declarations (%rowtype) are not lookups; every query must follow the check
            if body[match.end() : match.end() + 8] == "%rowtype":
                continue
            assert match.start() > first_check, (name, relation)
    assert "raise exception 'caller is not an allowlisted snapshot verifier for this workspace'" in body


def test_rpcs_only_read_or_publish_rows_of_the_requested_workspace():
    assert "snapshot.workspace_id = p_workspace_id" in _function_sql("begin_snapshot_verification")
    publish = _function_sql("publish_verified_snapshot")
    assert publish.count("snapshot.workspace_id = p_workspace_id") >= 1
    assert "snapshot.workspace_id = staged.workspace_id" in publish


def test_storage_helper_and_binding_trigger_use_the_staged_rows_workspace_scope():
    helper = _function_sql("verifier_may_read_staged_object")
    assert "snapshot.workspace_id = allowed.workspace_id" in helper
    assert "allowed.user_id = private.verified_user_id()" in helper
    trigger = re.search(
        r"function private\.require_verification_binding\(\).*?\$function\$;", MIGRATION, re.S
    ).group(0)
    assert "allowed.user_id = new.verified_by" in trigger
    assert "allowed.workspace_id = new.workspace_id" in trigger


def test_no_rpc_accepts_a_caller_chosen_identity_or_scope():
    for name in ("begin_snapshot_verification", "publish_verified_snapshot", "verifier_may_read_staged_object"):
        signature = re.search(rf"function public\.{name}\((.*?)\)\s*returns", _function_sql(name), re.S).group(1)
        assert "user" not in signature and "verifier" not in signature and "role" not in signature


# --- Older tests must exercise denial or the scoped RPC, never the old route --

_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    ["supabase/tests/source_snapshots.test.sql", "supabase/tests/source_snapshot_storage.test.sql"],
)
def test_older_pgtap_files_publish_only_through_the_scoped_rpc(path):
    text = (_ROOT / path).read_text()
    assert "publish_verified_snapshot" in text
    assert "snapshot_verifier_allowlist" in text
    # No test may open the legacy role for publication (it may only be assumed
    # by the denial checks, which never need membership).
    assert "grant snapshot_verifier to postgres" not in text
    assert not re.search(r"set local role snapshot_verifier;\s*select pg_temp\.(cnt|try)\('verifier_", text)


def test_live_test_lists_the_verifier_per_workspace_and_keeps_the_legacy_route_denied():
    text = (_ROOT / "tests/integration/test_local_storage_snapshot_policies.py").read_text()
    assert "set_verifier_enabled" not in text
    assert "private.snapshot_verifier_allowlist (user_id, workspace_id)" in text
    assert "test_verifier_scope_is_limited_to_its_listed_workspace" in text
    assert "test_legacy_role_route_is_closed_even_if_the_role_were_assumable" in text
