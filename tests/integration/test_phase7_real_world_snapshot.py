from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evidence_layer.source_access import SourceReadError
from evidence_service.auth import AuthenticatedRequest, Principal


def test_phase7_reads_real_world_passages_only_after_snapshot_acl_grant(stack):
    """Exercise the existing authenticated, ACL-checked read path for Phase 7.

    The optional output is a temporary handoff to the local benchmark runner.
    It contains the public benchmark corpus bytes and safe metadata only.
    """
    output_setting = os.environ.get("PHASE7_SECURE_SNAPSHOT_OUTPUT")
    if not output_setting:
        pytest.skip("Phase 7 secure snapshot handoff was not requested")

    repository = Path(__file__).resolve().parents[2]
    original = (repository / "data/real_world/passages.jsonl").read_bytes()
    expected_digest = hashlib.sha256(original).hexdigest()

    owner_token, _owner_id = stack.signup("phase7-snapshot-owner")
    workspace_id = stack.personal_workspace(owner_token)
    reader_token, reader_id = stack.signup("phase7-snapshot-reader")
    verifier_token, verifier_id = stack.signup("phase7-snapshot-verifier")
    stack.add_member(workspace_id, reader_id)

    source_id, version_id = str(uuid4()), str(uuid4())
    object_name = f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"
    staged = stack.stage(
        owner_token, workspace_id, source_id, version_id, original
    )
    assert staged.status_code == 201
    uploaded = stack.upload(owner_token, object_name, original)
    assert uploaded.status_code == 200

    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        stored = stack.read(verifier_token, object_name)
        assert stored.status_code == 200
        assert stored.content == original
        published = stack.publish(
            verifier_token,
            workspace_id,
            source_id,
            version_id,
            digest=hashlib.sha256(stored.content).hexdigest(),
            size=len(stored.content),
        )
        assert published.status_code in {200, 204}
    finally:
        stack.set_verifier_listed(False, verifier_id, workspace_id)

    path = stack.secure_read_path(reader_token)
    request = AuthenticatedRequest(
        Principal(issuer=f"{stack.api}/auth/v1", subject=UUID(reader_id)),
        reader_token,
    )
    arguments = {
        "workspace_id": UUID(workspace_id),
        "source_id": UUID(source_id),
        "version_id": UUID(version_id),
    }

    with pytest.raises(SourceReadError):
        asyncio.run(path.read(request, **arguments))

    grant = stack.grant(owner_token, workspace_id, source_id, reader_id)
    assert grant.status_code == 201
    started = time.perf_counter()
    result = asyncio.run(path.read(request, **arguments))
    read_latency_ms = (time.perf_counter() - started) * 1000

    assert result.content == original
    assert result.manifest.sha256 == expected_digest
    assert result.manifest.size_bytes == len(original)
    assert hashlib.sha256(result.content).hexdigest() == expected_digest

    envelope = {
        "content_base64": base64.b64encode(result.content).decode("ascii"),
        "sha256": result.manifest.sha256,
        "size_bytes": result.manifest.size_bytes,
        "read_latency_ms": read_latency_ms,
        "workspace_member_verified": True,
        "source_acl_grant_verified": True,
        "snapshot_digest_verified": True,
    }
    output = Path(output_setting)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")
