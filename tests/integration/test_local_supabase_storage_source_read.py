from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from evidence_layer.source_access import SourceReadError
from evidence_service.auth import AuthenticatedRequest, AuthenticationRejected, Principal


def test_real_local_storage_api_reads_exact_granted_snapshot_and_rechecks_revocation(stack):
    owner_token = stack.signup("snapshot-owner")[0]
    workspace_id = stack.personal_workspace(owner_token)
    reader_token, reader_id = stack.signup("snapshot-reader")
    verifier_token, verifier_id = stack.signup("snapshot-verifier")
    stack.add_member(workspace_id, reader_id)

    source_id, version_id = str(uuid4()), str(uuid4())
    object_name = stack_object_key(workspace_id, source_id, version_id)
    original = b"live Supabase Storage snapshot with an exact SHA-256 digest"
    expected_digest = hashlib.sha256(original).hexdigest()

    assert stack.stage(
        owner_token, workspace_id, source_id, version_id, original
    ).status_code == 201
    uploaded = stack.upload(owner_token, object_name, original)
    assert uploaded.status_code == 200
    assert stack.read(owner_token, object_name).status_code in {400, 403, 404}
    assert stack.read(reader_token, object_name).status_code in {400, 403, 404}

    stack.set_verifier_listed(True, verifier_id, workspace_id)
    try:
        stored = stack.read(verifier_token, object_name)
        assert stored.status_code == 200 and stored.content == original
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

    assert stack.read(owner_token, object_name).status_code in {400, 403, 404}
    assert stack.read(reader_token, object_name).status_code in {400, 403, 404}
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

    assert stack.grant(owner_token, workspace_id, source_id, reader_id).status_code == 201
    result = asyncio.run(path.read(request, **arguments))
    assert result.content == original
    assert result.manifest.sha256 == expected_digest
    assert result.manifest.size_bytes == len(original)
    assert hashlib.sha256(result.content).hexdigest() == expected_digest
    assert stack.read(reader_token, object_name).content == original

    assert stack.upload(
        owner_token, object_name, b"replacement", upsert=True
    ).status_code in {400, 403}
    assert stack.replace(owner_token, object_name, b"replacement").status_code in {
        400,
        403,
        404,
    }

    assert stack.revoke(owner_token, workspace_id, source_id, reader_id).status_code in {
        200,
        204,
    }
    assert stack.read(reader_token, object_name).status_code in {400, 403, 404}
    with pytest.raises(SourceReadError):
        asyncio.run(path.read(request, **arguments))


def test_local_auth_jwks_verifies_a_real_confirmed_identity_and_rejects_forgery(stack):
    access_token, user_id = stack.signup("jwks-confirmed-user")

    async def verify():
        return await stack.verifier.verify_access_token(access_token)

    verified = asyncio.run(verify())
    assert verified.principal.subject == UUID(user_id)
    assert verified.principal.issuer == f"{stack.api}/auth/v1"
    assert verified.expires_at.timestamp() > time.time()

    header = jwt.get_unverified_header(access_token)
    claims = jwt.decode(
        access_token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_aud": False,
            "verify_iss": False,
        },
    )
    algorithm = header["alg"]
    attacker_key = (
        ec.generate_private_key(ec.SECP256R1())
        if algorithm == "ES256"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    forged_signature = jwt.encode(
        claims,
        attacker_key,
        algorithm=algorithm,
        headers={"kid": header["kid"], "typ": "JWT"},
    )
    unknown_key = jwt.encode(
        claims,
        attacker_key,
        algorithm=algorithm,
        headers={"kid": "unknown-live-test-key", "typ": "JWT"},
    )
    hs256 = jwt.encode(
        claims,
        "local-test-only-not-a-project-key",
        algorithm="HS256",
        headers={"kid": header["kid"], "typ": "JWT"},
    )

    for token in (forged_signature, unknown_key, hs256):
        with pytest.raises(AuthenticationRejected):
            asyncio.run(stack.verifier.verified_claims(token))

    for name, value in (
        ("iss", "http://127.0.0.1:1/auth/v1"),
        ("aud", "anon"),
        ("exp", int(time.time()) - 60),
    ):
        changed = dict(claims)
        changed[name] = value
        encoded = base64.urlsafe_b64encode(
            json.dumps(changed, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        tampered = f"{access_token.split('.')[0]}.{encoded}.{access_token.split('.')[2]}"
        with pytest.raises(AuthenticationRejected):
            asyncio.run(stack.verifier.verified_claims(tampered))


def stack_object_key(workspace_id: str, source_id: str, version_id: str) -> str:
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"
