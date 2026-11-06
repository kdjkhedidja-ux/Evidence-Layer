"""End-to-end HTTP proof that the live service enforces the secure read path.

This module runs the real FastAPI application (``create_app``) on a loopback
uvicorn server, composed entirely against the disposable local Supabase stack.
It proves the full request path from a bearer JWT to delivered snapshot bytes,
including closed rejection for every security failure, and that no response
ever carries rejected content.

The stack is provided by ``tests/integration/run_local_supabase.py``; without it
the required environment is absent and collection fails closed (no skip).
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn

from evidence_service.app import create_app
from evidence_service.config import SupabaseSettings
from account_lifecycle_test_support import OpenRecoveryFenceReader

CONTENT = b"end-to-end authorized snapshot bytes with an exact sha-256 digest"


def _object_key(workspace_id: str, source_id: str, version_id: str) -> str:
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


def _service_settings(stack) -> SupabaseSettings:
    # The default 3s Auth/Storage timeout assumes a healthy host; the disposable
    # two-CPU Codespaces stack serves JWKS/Storage slowly under load. A longer
    # timeout changes only how long the service waits, never a security decision
    # (every check still fails closed on error).
    return SupabaseSettings(
        supabase_url=stack.api,
        publishable_key=stack.key,
        jwt_issuer=f"{stack.api}/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256", "RS256"),
        timeout_seconds=20.0,
    )


@pytest.fixture(scope="module")
def http_service(stack):
    """Run the real app on loopback, composed against the isolated Supabase.

    The composition passes no token verifier, directory, repository, or read
    path override, so the app builds its own trusted Supabase-backed adapters —
    the same wiring a production composition would use, bound to the isolated
    project only.
    """
    app = create_app(
        _service_settings(stack), recovery_fence_reader=OpenRecoveryFenceReader()
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("The test HTTP service did not start")

    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=30.0, trust_env=False)
    try:
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=10)


def _content_url(workspace_id: str, source_id: str, version_id: str) -> str:
    return (
        f"/v1/workspaces/{workspace_id}/sources/{source_id}"
        f"/versions/{version_id}/content"
    )


@pytest.fixture(scope="module")
def provisioned(stack):
    """Stage, upload, verify, publish, and grant one real snapshot."""
    owner_token = stack.signup("http-owner")[0]
    workspace_id = stack.personal_workspace(owner_token)
    reader_token, reader_id = stack.signup("http-reader")
    verifier_token, verifier_id = stack.signup("http-verifier")
    stack.add_member(workspace_id, reader_id)

    source_id, version_id = str(uuid4()), str(uuid4())
    key = _object_key(workspace_id, source_id, version_id)
    digest = hashlib.sha256(CONTENT).hexdigest()

    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, CONTENT
    ).status_code == 201
    assert stack.upload(owner_token, key, CONTENT).status_code == 200

    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        stored = stack.read(verifier_token, key)
        assert stored.status_code == 200 and stored.content == CONTENT
        assert stack.publish(
            verifier_token,
            workspace_id,
            source_id,
            version_id,
            digest=hashlib.sha256(stored.content).hexdigest(),
            size=len(stored.content),
        ).status_code in {200, 204}
    finally:
        stack.set_verifier_listed(False, verifier_id, workspace_id)

    assert stack.grant(
        owner_token, workspace_id, source_id, reader_id
    ).status_code == 201

    return {
        "workspace_id": workspace_id,
        "source_id": source_id,
        "version_id": version_id,
        "object_key": key,
        "digest": digest,
        "reader_token": reader_token,
        "reader_id": reader_id,
        "owner_token": owner_token,
    }


def test_http_authorized_member_receives_verified_content(http_service, provisioned):
    response = http_service.get(
        _content_url(
            provisioned["workspace_id"],
            provisioned["source_id"],
            provisioned["version_id"],
        ),
        headers={"Authorization": f"Bearer {provisioned['reader_token']}"},
    )
    assert response.status_code == 200, response.text
    assert response.content == CONTENT
    assert response.headers["content-digest"] == (
        f"sha-256=:{provisioned['digest']}:"
    )
    assert hashlib.sha256(response.content).hexdigest() == provisioned["digest"]


def test_http_rejects_missing_and_invalid_token(http_service, provisioned):
    url = _content_url(
        provisioned["workspace_id"],
        provisioned["source_id"],
        provisioned["version_id"],
    )
    assert http_service.get(url).status_code == 401
    assert http_service.get(
        url, headers={"Authorization": "Bearer not-a-real-token"}
    ).status_code == 401


def test_http_rejects_user_without_workspace_membership(http_service, stack, provisioned):
    outsider_token = stack.signup("http-outsider")[0]
    response = http_service.get(
        _content_url(
            provisioned["workspace_id"],
            provisioned["source_id"],
            provisioned["version_id"],
        ),
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    # A non-member is indistinguishable from an unknown workspace.
    assert response.status_code == 404
    assert response.content != CONTENT


def test_http_rejects_member_without_source_grant(http_service, stack, provisioned):
    member_token, member_id = stack.signup("http-member-nogrant")
    stack.add_member(provisioned["workspace_id"], member_id)
    response = http_service.get(
        _content_url(
            provisioned["workspace_id"],
            provisioned["source_id"],
            provisioned["version_id"],
        ),
        headers={"Authorization": f"Bearer {member_token}"},
    )
    # Membership alone never authorizes a source read. The service answers a
    # closed denial (403) or, because the catalog row is invisible to a reader
    # without a grant under RLS, a not-found (404). Neither leaks content.
    assert response.status_code in {403, 404}
    assert response.content != CONTENT


def test_http_rejects_unpublished_snapshot(http_service, stack, provisioned):
    # A staged-but-never-published version is not readable through the service.
    owner_token = provisioned["owner_token"]
    workspace_id = provisioned["workspace_id"]
    source_id, version_id = str(uuid4()), str(uuid4())
    key = _object_key(workspace_id, source_id, version_id)
    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, CONTENT
    ).status_code == 201
    assert stack.upload(owner_token, key, CONTENT).status_code == 200
    reader_token, reader_id = stack.signup("http-stagedreader")
    stack.add_member(workspace_id, reader_id)
    assert stack.grant(owner_token, workspace_id, source_id, reader_id).status_code == 201

    response = http_service.get(
        _content_url(workspace_id, source_id, version_id),
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert response.status_code in {403, 404}
    assert response.content != CONTENT


def test_http_denies_after_grant_revocation(http_service, stack, provisioned):
    # A member whose grant is revoked between reads gets no content afterward.
    revoke_token, revoke_id = stack.signup("http-revokereader")
    workspace_id = provisioned["workspace_id"]
    stack.add_member(workspace_id, revoke_id)
    assert stack.grant(
        provisioned["owner_token"], workspace_id, provisioned["source_id"], revoke_id
    ).status_code == 201

    url = _content_url(
        workspace_id, provisioned["source_id"], provisioned["version_id"]
    )
    allowed = http_service.get(url, headers={"Authorization": f"Bearer {revoke_token}"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.content == CONTENT

    assert stack.revoke(
        provisioned["owner_token"], workspace_id, provisioned["source_id"], revoke_id
    ).status_code in {200, 204}

    denied = http_service.get(url, headers={"Authorization": f"Bearer {revoke_token}"})
    # After revocation the catalog row is invisible to the reader (RLS), so the
    # service answers not-found; the content is never delivered either way.
    assert denied.status_code in {403, 404}
    assert denied.content != CONTENT


def test_http_content_with_mismatched_staged_digest_is_never_delivered(
    http_service, stack, provisioned
):
    # Stage a digest for one payload but upload different bytes. Publication is
    # rejected by the verifier guard, so the version never becomes published and
    # the service must never deliver the bytes through the content route.
    from conftest import is_rejected

    owner_token = provisioned["owner_token"]
    workspace_id = provisioned["workspace_id"]
    source_id, version_id = str(uuid4()), str(uuid4())
    key = _object_key(workspace_id, source_id, version_id)
    staged = b"the digest recorded in the catalog"
    different = b"different bytes than the catalog digest"

    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, staged
    ).status_code == 201
    assert stack.upload(owner_token, key, different).status_code == 200

    verifier_token, verifier_id = stack.signup("http-tamper-verifier")
    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        stored = stack.read(verifier_token, key)
        assert stored.status_code == 200
        published = stack.publish(
            verifier_token,
            workspace_id,
            source_id,
            version_id,
            digest=hashlib.sha256(staged).hexdigest(),
            size=len(staged),
        )
        # The staged digest does not match the uploaded bytes; closed rejection.
        assert is_rejected(published, postgrest_code="P0002")
        assert stack.catalog_state(owner_token, version_id)[0]["state"] == "staged"
    finally:
        stack.set_verifier_listed(False, verifier_id, workspace_id)

    reader_token, reader_id = stack.signup("http-tamperreader")
    stack.add_member(workspace_id, reader_id)
    assert stack.grant(owner_token, workspace_id, source_id, reader_id).status_code == 201

    response = http_service.get(
        _content_url(workspace_id, source_id, version_id),
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert response.status_code in {403, 404, 503}
    assert response.content != staged
    assert response.content != different


def test_http_no_alternate_content_route_bypasses_the_guard(http_service, provisioned):
    # There is exactly one content-serving route and it requires the bearer +
    # membership + grant. Confirm no unauthenticated or path variant leaks bytes.
    base = (
        f"/v1/workspaces/{provisioned['workspace_id']}"
        f"/sources/{provisioned['source_id']}/versions/{provisioned['version_id']}"
    )
    for path in (
        f"{base}/content",
        f"{base}/raw",
        f"{base}/download",
        f"{base}/content/raw",
        f"{base}",
    ):
        unauthenticated = http_service.get(path)
        assert unauthenticated.status_code in {401, 404, 405}, path
        assert unauthenticated.content != CONTENT, path
