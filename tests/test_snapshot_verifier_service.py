import ast
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from evidence_layer.snapshot_verifier import (
    LocalSupabaseVerificationGateway,
    SnapshotVerificationError,
    SnapshotVerificationService,
    StagedSnapshotClaim,
    SupabaseStorageSnapshotReader,
    VerificationFailure,
    snapshot_object_key,
)
from snapshot_verifier_service import cli
from snapshot_verifier_service.config import (
    ACCESS_TOKEN_ENV,
    PUBLISHABLE_KEY_ENV,
    SUPABASE_URL_ENV,
    VerifierSettings,
)

WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
OBJECT_ID = UUID("f1000000-0000-4000-8000-000000000001")
OBJECT_VERSION = "opaque-storage-version"
TOKEN = "synthetic-verifier-user-token"
API_KEY = "sb_publishable_local-test-key"
BASE_URL = "http://127.0.0.1:55321"
CONTENT = b"bytes returned by the mocked Storage API\n"
KEY = snapshot_object_key(WORKSPACE, SOURCE, VERSION)


def _jwt(role: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"e30.{payload}.signature"


def _settings(**override):
    values = {
        SUPABASE_URL_ENV: BASE_URL,
        PUBLISHABLE_KEY_ENV: API_KEY,
        ACCESS_TOKEN_ENV: TOKEN,
    }
    values.update(override)
    return values


def _claim(content=CONTENT):
    return StagedSnapshotClaim(
        workspace_id=WORKSPACE,
        source_id=SOURCE,
        version_id=VERSION,
        object_key=KEY,
        claimed_sha256=hashlib.sha256(content).hexdigest(),
        claimed_size_bytes=len(content),
        storage_object_id=OBJECT_ID,
        storage_object_version=OBJECT_VERSION,
    )


def test_standalone_flow_hashes_bytes_downloaded_from_storage_and_publishes_only_ids_and_evidence():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/rest/v1/rpc/begin_snapshot_verification":
            return httpx.Response(
                200,
                json=[
                    {
                        "out_object_key": KEY,
                        "out_claimed_sha256": hashlib.sha256(CONTENT).hexdigest(),
                        "out_claimed_size_bytes": len(CONTENT),
                        "out_storage_object_id": str(OBJECT_ID),
                        "out_storage_object_version": OBJECT_VERSION,
                    }
                ],
            )
        if request.url.path == f"/storage/v1/object/source-snapshots/{KEY}":
            return httpx.Response(
                200,
                headers={"content-length": str(len(CONTENT))},
                content=CONTENT,
            )
        if request.url.path == "/rest/v1/rpc/publish_verified_snapshot":
            return httpx.Response(204)
        pytest.fail("unexpected local HTTP request")

    transport = httpx.MockTransport(handler)
    gateway_client = httpx.AsyncClient(transport=transport)
    storage_client = httpx.AsyncClient(transport=transport)
    gateway = LocalSupabaseVerificationGateway(
        base_url=BASE_URL, publishable_key=API_KEY, client=gateway_client
    )
    reader = SupabaseStorageSnapshotReader(
        base_url=BASE_URL, publishable_key=API_KEY, client=storage_client
    )
    service = SnapshotVerificationService(gateway=gateway, reader=reader)

    async def verify():
        try:
            return await service.verify_and_publish(
                TOKEN, workspace_id=WORKSPACE, source_id=SOURCE, version_id=VERSION
            )
        finally:
            await gateway_client.aclose()
            await storage_client.aclose()

    result = asyncio.run(verify())
    assert result.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert result.size_bytes == len(CONTENT)
    assert [request.method for request in requests] == ["POST", "GET", "POST"]
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert requests[1].headers["authorization"] == f"Bearer {TOKEN}"
    assert requests[1].headers["accept-encoding"] == "identity"
    assert json.loads(requests[0].content) == {
        "p_workspace_id": str(WORKSPACE),
        "p_source_id": str(SOURCE),
        "p_version_id": str(VERSION),
    }
    assert json.loads(requests[2].content) == {
        "p_workspace_id": str(WORKSPACE),
        "p_source_id": str(SOURCE),
        "p_version_id": str(VERSION),
        "p_sha256": hashlib.sha256(CONTENT).hexdigest(),
        "p_size_bytes": len(CONTENT),
        "p_storage_object_id": str(OBJECT_ID),
        "p_storage_object_version": OBJECT_VERSION,
    }


@pytest.mark.parametrize("status", [401, 403])
def test_storage_denial_fails_closed_before_publication(status):
    reader = SupabaseStorageSnapshotReader(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status))
        ),
    )

    async def run():
        try:
            await reader.read_exact(KEY, access_token=TOKEN)
        finally:
            await reader._client.aclose()

    with pytest.raises(SnapshotVerificationError) as error:
        asyncio.run(run())
    assert error.value.reason is VerificationFailure.NOT_AUTHORISED
    assert TOKEN not in str(error.value)


def test_storage_body_limit_is_enforced_while_streaming():
    reader = SupabaseStorageSnapshotReader(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        max_snapshot_bytes=8,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 9))
        ),
    )

    async def run():
        try:
            await reader.read_exact(KEY, access_token=TOKEN)
        finally:
            await reader._client.aclose()

    with pytest.raises(SnapshotVerificationError) as error:
        asyncio.run(run())
    assert error.value.reason is VerificationFailure.SIZE_MISMATCH


def test_noncanonical_object_key_is_rejected_before_storage_request():
    requests = []
    reader = SupabaseStorageSnapshotReader(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, content=CONTENT)
            )
        ),
    )

    async def run():
        try:
            await reader.read_exact("../private/other", access_token=TOKEN)
        finally:
            await reader._client.aclose()

    with pytest.raises(SnapshotVerificationError) as error:
        asyncio.run(run())
    assert error.value.reason is VerificationFailure.INVALID_REQUEST
    assert requests == []


def test_runtime_configuration_requires_injected_local_credentials_and_rejects_remote_hosts():
    with pytest.raises(ValueError, match="credentials are missing"):
        VerifierSettings.from_environment({})
    with pytest.raises(ValueError, match="configuration is invalid"):
        VerifierSettings.from_environment(
            _settings(**{SUPABASE_URL_ENV: "https://project.supabase.co"})
        )
    with pytest.raises(ValueError, match="credentials are invalid"):
        VerifierSettings.from_environment(_settings(**{ACCESS_TOKEN_ENV: _jwt("service_role")}))


def test_cli_does_not_accept_client_digest_size_or_storage_key():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "--workspace-id", str(WORKSPACE),
                "--source-id", str(SOURCE),
                "--version-id", str(VERSION),
                "--sha256", "0" * 64,
                "--size-bytes", "1",
            ]
        )


def test_cli_configuration_errors_and_verification_errors_do_not_print_credentials(
    capsys, caplog, monkeypatch
):
    missing = cli.main(
        ["--workspace-id", str(WORKSPACE), "--source-id", str(SOURCE), "--version-id", str(VERSION)],
        environment={},
    )
    captured = capsys.readouterr()
    assert missing == 2
    assert captured.err == "snapshot verifier configuration invalid\n"
    assert TOKEN not in captured.out + captured.err
    assert not caplog.records

    async def fail_with_fixed_error(*args, **kwargs):
        raise SnapshotVerificationError(VerificationFailure.STORAGE_UNAVAILABLE)

    monkeypatch.setattr(cli, "_verify", fail_with_fixed_error)
    failed = cli.main(
        ["--workspace-id", str(WORKSPACE), "--source-id", str(SOURCE), "--version-id", str(VERSION)],
        environment=_settings(),
    )
    captured = capsys.readouterr()
    assert failed == 1
    assert captured.err == "snapshot verification failed: storage_unavailable\n"
    assert TOKEN not in captured.out + captured.err
    assert API_KEY not in captured.out + captured.err
    assert not caplog.records


def test_standalone_runner_has_no_web_app_or_evidence_service_imports():
    root = Path(__file__).resolve().parents[1]
    service_root = root / "services/snapshot-verifier/snapshot_verifier_service"
    imported = set()
    for path in service_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(
        name == "evidence_service"
        or name.startswith(("evidence_service.", "fastapi", "starlette", "uvicorn"))
        for name in imported
    )
