"""Live proof that verified retrieval reads only through the secure path.

These tests run ``retrieve_verified_evidence`` against the real
``SecureSnapshotReadPath`` (composed by ``stack.secure_read_path``) on the
disposable isolated Supabase stack — not a mocked read path. They prove that
retrieval returns ranked evidence only from fully authorized, published,
SHA-256-verified snapshots, fails closed when authorization is absent or
revoked, and never leaks partial results across a multi-source batch.

No user-facing ``/retrieve`` route and no external egress is activated here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from evidence_layer.source_access import SourceReadError
from evidence_service.auth import AuthenticatedRequest, Principal
from evidence_service.verified_retrieval import retrieve_verified_evidence


def _record(passage_id: str, source_id: str, text: str, url: str) -> dict:
    return {
        "passage_id": passage_id,
        "source_id": source_id,
        "source_url": url,
        "text": text,
    }


def _jsonl(records: list[dict]) -> bytes:
    return "".join(json.dumps(record) + "\n" for record in records).encode()


def _stage_upload_publish_grant(
    stack,
    *,
    owner_token: str,
    workspace_id: str,
    source_id: str,
    version_id: str,
    content: bytes,
    verifier_token: str,
    verifier_id: str,
    grant_to: str,
) -> str:
    """Stage, upload, publish, and grant one snapshot; return its object key."""
    key = f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"
    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, content
    ).status_code == 201
    assert stack.upload(owner_token, key, content).status_code == 200

    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        stored = stack.read(verifier_token, key)
        assert stored.status_code == 200 and stored.content == content
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
        owner_token, workspace_id, source_id, grant_to
    ).status_code == 201
    return key


@pytest.fixture(scope="module")
def retrieval_env(stack):
    """Two published sources, a reader with grants to both, and a revoked one."""
    owner_token = stack.signup("vr-owner")[0]
    workspace_id = stack.personal_workspace(owner_token)
    reader_token, reader_id = stack.signup("vr-reader")
    verifier_token, verifier_id = stack.signup("vr-verifier")
    stack.add_member(workspace_id, reader_id)

    source_a, version_a = str(uuid4()), str(uuid4())
    source_b, version_b = str(uuid4()), str(uuid4())
    content_a = _jsonl(
        [
            _record("a1", source_a, "alpha beta gamma evidence", "https://a.test/1"),
            _record("a2", source_a, "alpha delta passage", "https://a.test/2"),
        ]
    )
    content_b = _jsonl(
        [
            _record("b1", source_b, "alpha beta separate source", "https://b.test/1"),
        ]
    )

    _stage_upload_publish_grant(
        stack,
        owner_token=owner_token,
        workspace_id=workspace_id,
        source_id=source_a,
        version_id=version_a,
        content=content_a,
        verifier_token=verifier_token,
        verifier_id=verifier_id,
        grant_to=reader_id,
    )
    _stage_upload_publish_grant(
        stack,
        owner_token=owner_token,
        workspace_id=workspace_id,
        source_id=source_b,
        version_id=version_b,
        content=content_b,
        verifier_token=verifier_token,
        verifier_id=verifier_id,
        grant_to=reader_id,
    )

    return {
        "workspace_id": workspace_id,
        "reader_token": reader_token,
        "reader_id": reader_id,
        "owner_token": owner_token,
        "source_a": source_a,
        "version_a": version_a,
        "source_b": source_b,
        "version_b": version_b,
    }


def _retrieve(stack, reader_token, reader_id, workspace_id, sources, query="alpha"):
    path = stack.secure_read_path(reader_token)
    request = AuthenticatedRequest(
        Principal(
            issuer=f"{stack.api}/auth/v1",
            subject=UUID(reader_id),
        ),
        reader_token,
    )
    return asyncio.run(
        retrieve_verified_evidence(
            path,
            request,
            workspace_id=UUID(workspace_id),
            sources=[(UUID(s), UUID(v)) for s, v in sources],
            query=query,
        )
    )


def test_retrieval_succeeds_for_authorized_sources(stack, retrieval_env):
    result = _retrieve(
        stack,
        retrieval_env["reader_token"],
        retrieval_env["reader_id"],
        retrieval_env["workspace_id"],
        [
            (retrieval_env["source_a"], retrieval_env["version_a"]),
            (retrieval_env["source_b"], retrieval_env["version_b"]),
        ],
    )
    assert result.status == "ok"
    assert result.evidence, "expected ranked evidence from verified sources"
    # Every returned record traces back to one of the two authorized sources.
    allowed = {retrieval_env["source_a"], retrieval_env["source_b"]}
    assert all(item.source_id in allowed for item in result.evidence)
    assert all(item.score is not None for item in result.evidence)


def test_retrieval_denied_without_source_grant(stack, retrieval_env):
    # A workspace member with no grant to source_b must not retrieve it.
    no_grant_token, no_grant_id = stack.signup("vr-nogrant")
    stack.add_member(retrieval_env["workspace_id"], no_grant_id)
    # Grant only source_a; source_b has no grant for this reader.
    assert stack.grant(
        retrieval_env["owner_token"],
        retrieval_env["workspace_id"],
        retrieval_env["source_a"],
        no_grant_id,
    ).status_code == 201

    with pytest.raises(SourceReadError):
        _retrieve(
            stack,
            no_grant_token,
            no_grant_id,
            retrieval_env["workspace_id"],
            [
                (retrieval_env["source_a"], retrieval_env["version_a"]),
                (retrieval_env["source_b"], retrieval_env["version_b"]),
            ],
        )


def test_retrieval_denied_for_non_member(stack, retrieval_env):
    outsider_token, outsider_id = stack.signup("vr-outsider")
    with pytest.raises(SourceReadError):
        _retrieve(
            stack,
            outsider_token,
            outsider_id,
            retrieval_env["workspace_id"],
            [(retrieval_env["source_a"], retrieval_env["version_a"])],
        )


def test_retrieval_fails_closed_on_revocation_and_leaks_no_partial_results(
    stack, retrieval_env
):
    # Grant source_a to a fresh reader, confirm a successful read, then revoke
    # it and confirm the whole retrieval fails closed with no partial output.
    token, rid = stack.signup("vr-revoke")
    stack.add_member(retrieval_env["workspace_id"], rid)
    assert stack.grant(
        retrieval_env["owner_token"],
        retrieval_env["workspace_id"],
        retrieval_env["source_a"],
        rid,
    ).status_code == 201

    ok = _retrieve(
        stack,
        token,
        rid,
        retrieval_env["workspace_id"],
        [(retrieval_env["source_a"], retrieval_env["version_a"])],
    )
    assert ok.status == "ok" and ok.evidence

    assert stack.revoke(
        retrieval_env["owner_token"],
        retrieval_env["workspace_id"],
        retrieval_env["source_a"],
        rid,
    ).status_code in {200, 204}

    with pytest.raises(SourceReadError):
        _retrieve(
            stack,
            token,
            rid,
            retrieval_env["workspace_id"],
            [(retrieval_env["source_a"], retrieval_env["version_a"])],
        )


def test_retrieval_rejects_tampered_snapshot_content(stack, retrieval_env):
    # Stage a digest for one payload but upload different bytes; publication is
    # rejected, so retrieval over that version must fail closed and never rank
    # the bytes.
    from conftest import is_rejected

    owner_token = retrieval_env["owner_token"]
    workspace_id = retrieval_env["workspace_id"]
    source_id, version_id = str(uuid4()), str(uuid4())
    key = f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"
    staged = _jsonl([_record("t1", source_id, "alpha tamper staged", "https://t.test/1")])
    different = _jsonl([_record("t1", source_id, "alpha tamper changed", "https://t.test/1")])

    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, staged
    ).status_code == 201
    assert stack.upload(owner_token, key, different).status_code == 200

    verifier_token, verifier_id = stack.signup("vr-tamper-verifier")
    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        published = stack.publish(
            verifier_token,
            workspace_id,
            source_id,
            version_id,
            digest=hashlib.sha256(staged).hexdigest(),
            size=len(staged),
        )
        assert is_rejected(published, postgrest_code="P0002")
        assert stack.catalog_state(owner_token, version_id)[0]["state"] == "staged"
    finally:
        stack.set_verifier_listed(False, verifier_id, workspace_id)

    reader_token, reader_id = stack.signup("vr-tamper-reader")
    stack.add_member(workspace_id, reader_id)
    assert stack.grant(
        owner_token, workspace_id, source_id, reader_id
    ).status_code == 201

    with pytest.raises(SourceReadError):
        _retrieve(
            stack,
            reader_token,
            reader_id,
            workspace_id,
            [(source_id, version_id)],
        )
