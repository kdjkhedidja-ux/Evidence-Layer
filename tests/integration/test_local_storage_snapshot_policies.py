"""Live tests of source-snapshot catalog, Storage policies and verifier scope.

These tests run only inside the disposable local project created by
``run_local_supabase.py``. Its stack is stopped with ``--no-backup`` after the
test run, removing confirmed Auth users, immutable catalog rows and Storage
objects together without weakening their production policies.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from conftest import is_rejected
from evidence_layer.source_access import SourceReadError
from evidence_service.auth import AuthenticatedRequest, Principal


@pytest.fixture(scope="module")
def actors(stack):
    owner, owner_id = stack.signup("policy-owner")
    workspace_id = stack.personal_workspace(owner)
    granted, granted_id = stack.signup("policy-granted")
    ungranted, ungranted_id = stack.signup("policy-ungranted")
    outsider, outsider_id = stack.signup("policy-outsider")
    outsider_workspace_id = stack.personal_workspace(outsider)
    verifier, verifier_id = stack.signup("policy-verifier")
    stack.add_member(workspace_id, granted_id)
    stack.add_member(workspace_id, ungranted_id)
    return {
        "owner": owner,
        "owner_id": owner_id,
        "workspace_id": workspace_id,
        "granted": granted,
        "granted_id": granted_id,
        "ungranted": ungranted,
        "ungranted_id": ungranted_id,
        "outsider": outsider,
        "outsider_id": outsider_id,
        "outsider_workspace_id": outsider_workspace_id,
        "verifier": verifier,
        "verifier_id": verifier_id,
    }


@pytest.fixture
def verifier_enabled(stack, actors):
    stack.set_verifier_listed(True, actors["verifier_id"], actors["workspace_id"])
    try:
        yield
    finally:
        stack.set_verifier_listed(False, actors["verifier_id"], actors["workspace_id"])


def new_version(stack, actors, data: bytes, source_id: str | None = None):
    source_id = source_id or str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    workspace_id = actors["workspace_id"]
    response = stack.stage(
        actors["owner"], workspace_id, source_id, version_id, data
    )
    assert response.status_code == 201
    return source_id, version_id, object_key(workspace_id, source_id, version_id)


def object_key(workspace_id: str, source_id: str, version_id: str) -> str:
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


def test_publication_is_closed_by_default(stack, actors):
    stack.set_verifier_listed(False, actors["verifier_id"], actors["workspace_id"])
    data = b"closed by default"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200

    token = actors["verifier"]
    workspace_id = actors["workspace_id"]
    assert stack.begin(token, workspace_id, source_id, version_id).status_code in {
        401,
        403,
        404,
    }
    assert stack.publish(
        token, workspace_id, source_id, version_id, digest=digest, size=len(data)
    ).status_code in {401, 403, 404}
    assert stack.catalog_state(token, version_id) == []
    assert stack.read(token, key).status_code in {400, 401, 403, 404}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_legacy_direct_publication_route_is_closed_for_auth_users(stack, actors):
    data = b"legacy direct publication stays closed"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200
    response = stack.client.patch(
        f"{stack.api}/rest/v1/source_snapshots",
        params={"version_id": f"eq.{version_id}"},
        headers=stack.headers(
            actors["verifier"],
            **{
                "content-type": "application/json",
                "prefer": "return=representation",
            },
        ),
        json={
            "state": "published",
            "verified_sha256": digest,
            "verified_size_bytes": len(data),
        },
    )
    assert response.status_code in {400, 401, 403, 404}
    assert stack.read(actors["verifier"], key).status_code in {400, 401, 403, 404}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_unpublished_object_is_unreadable_and_cannot_be_overwritten_or_deleted(
    stack, actors, verifier_enabled
):
    data = b"staged but never published"
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200

    for who in ("owner", "granted", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who
    stack.grant(actors["owner"], actors["workspace_id"], source_id, actors["granted_id"])
    assert stack.read(actors["granted"], key).status_code in {400, 403, 404}

    assert stack.upload(actors["owner"], key, b"evil", upsert=True).status_code in {
        400,
        403,
    }
    assert stack.upload(actors["owner"], key, b"evil").status_code in {400, 409}
    assert stack.replace(actors["owner"], key, b"evil").status_code in {
        400,
        403,
        404,
    }
    assert stack.delete(actors["owner"], key).status_code in {400, 403}
    stored = stack.read(actors["verifier"], key)
    assert stored.status_code == 200 and stored.content == data
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_upload_requires_a_staged_record_owned_by_the_uploader(stack, actors, verifier_enabled):
    data = b"payload"
    _, _, key = new_version(stack, actors, data)
    unrecorded = object_key(
        actors["workspace_id"], str(uuid.uuid4()), str(uuid.uuid4())
    )

    assert stack.upload(actors["owner"], unrecorded, data).status_code in {400, 403}
    for who in ("granted", "ungranted", "outsider"):
        assert stack.upload(actors[who], key, data).status_code in {400, 403}, who
    assert stack.upload(actors["verifier"], key, data).status_code in {400, 401, 403}
    assert stack.upload(actors["owner"], key, data).status_code == 200


def test_publication_requires_the_scoped_verifier_and_matching_attestation(
    stack, actors, verifier_enabled
):
    data = b"verified snapshot bytes"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    workspace_id = actors["workspace_id"]

    early = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest=digest, size=len(data),
    )
    assert is_rejected(early, postgrest_code="P0002")
    assert stack.upload(actors["owner"], key, data).status_code == 200

    assert stack.grant(
        actors["owner"], workspace_id, source_id, actors["owner_id"]
    ).status_code == 201
    owner_publish = stack.publish(
        actors["owner"], workspace_id, source_id, version_id,
        digest=digest, size=len(data),
    )
    assert owner_publish.status_code in {401, 403, 404}
    direct = stack.stage(
        actors["owner"], workspace_id, str(uuid.uuid4()), str(uuid.uuid4()), data,
        state="published",
    )
    assert direct.status_code in {400, 401, 403}
    forged = stack.stage(
        actors["owner"], workspace_id, str(uuid.uuid4()), str(uuid.uuid4()), data,
        verified_sha256=digest, verified_size_bytes=len(data),
    )
    assert forged.status_code in {400, 401, 403}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"
    assert stack.revoke(
        actors["owner"], workspace_id, source_id, actors["owner_id"]
    ).status_code in {200, 204}

    wrong = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest="0" * 64, size=len(data),
    )
    assert is_rejected(wrong, postgrest_code="P0002")
    wrong_size = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest=digest, size=len(data) + 1,
    )
    assert is_rejected(wrong_size, postgrest_code="P0002")

    claims = jwt.decode(
        actors["verifier"],
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_aud": False,
            "verify_iss": False,
        },
    )
    header = jwt.get_unverified_header(actors["verifier"])
    rogue_key = (
        ec.generate_private_key(ec.SECP256R1())
        if header["alg"] == "ES256"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    rogue = jwt.encode(
        claims,
        rogue_key,
        algorithm=header["alg"],
        headers={"kid": header["kid"], "typ": "JWT"},
    )
    assert stack.publish(
        rogue, workspace_id, source_id, version_id, digest=digest, size=len(data)
    ).status_code in {401, 403, 404}
    assert stack.read(rogue, key).status_code in {400, 401, 403, 404}

    stored = stack.read(actors["verifier"], key)
    assert stored.status_code == 200
    published = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest=hashlib.sha256(stored.content).hexdigest(), size=len(stored.content),
    )
    assert published.status_code in {200, 204}

    for who in ("owner", "granted", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who
    assert stack.grant(
        actors["owner"], workspace_id, source_id, actors["granted_id"]
    ).status_code == 201
    allowed = stack.read(actors["granted"], key)
    assert allowed.status_code == 200 and allowed.content == data
    assert hashlib.sha256(allowed.content).hexdigest() == digest
    assert stack.catalog_state(actors["granted"], version_id)[0]["verified_at"]
    for who in ("owner", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who

    assert stack.upload(actors["owner"], key, b"evil", upsert=True).status_code in {
        400,
        403,
    }
    assert stack.upload(actors["owner"], key, b"evil").status_code in {
        400,
        403,
        409,
    }
    assert stack.upload(actors["granted"], key, b"evil", upsert=True).status_code in {
        400,
        403,
    }
    assert stack.replace(actors["granted"], key, b"evil").status_code in {
        400,
        403,
        404,
    }
    assert stack.delete(actors["owner"], key).status_code in {400, 403}
    assert stack.delete(actors["granted"], key).status_code in {400, 403}
    republish = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest=digest, size=len(data),
    )
    assert is_rejected(republish, postgrest_code="P0002")
    assert stack.read(actors["granted"], key).content == data

    assert stack.revoke(
        actors["owner"], workspace_id, source_id, actors["granted_id"]
    ).status_code in {200, 204}
    assert stack.read(actors["granted"], key).status_code in {400, 403, 404}


def test_object_without_matching_catalog_record_is_never_publishable(
    stack, actors, verifier_enabled
):
    claimed = b"x" * 10
    source_id, version_id, key = new_version(stack, actors, claimed)
    assert stack.upload(actors["owner"], key, b"y" * 11).status_code == 200
    mismatch = stack.publish(
        actors["verifier"], actors["workspace_id"], source_id, version_id,
        digest=hashlib.sha256(claimed).hexdigest(), size=len(claimed),
    )
    assert is_rejected(mismatch, postgrest_code="P0002")
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_reader_recomputes_sha256_instead_of_trusting_the_attestation(
    stack, actors, verifier_enabled
):
    claimed = b"A" * 16
    actual = b"B" * 16
    source_id, version_id, key = new_version(stack, actors, claimed)
    assert stack.upload(actors["owner"], key, actual).status_code == 200

    published = stack.publish(
        actors["verifier"], actors["workspace_id"], source_id, version_id,
        digest=hashlib.sha256(claimed).hexdigest(), size=len(claimed),
    )
    assert published.status_code in {200, 204}
    assert stack.grant(
        actors["owner"], actors["workspace_id"], source_id, actors["granted_id"]
    ).status_code == 201
    served = stack.read(actors["granted"], key)
    assert served.status_code == 200
    assert stack.catalog_state(actors["granted"], version_id)[0]["state"] == "published"
    assert hashlib.sha256(served.content).hexdigest() != hashlib.sha256(claimed).hexdigest()

    read_path = stack.secure_read_path(actors["granted"])
    request = AuthenticatedRequest(
        Principal(
            issuer=f"{stack.api}/auth/v1",
            subject=uuid.UUID(actors["granted_id"]),
        ),
        actors["granted"],
    )
    with pytest.raises(SourceReadError):
        asyncio.run(
            read_path.read(
                request,
                workspace_id=uuid.UUID(actors["workspace_id"]),
                source_id=uuid.UUID(source_id),
                version_id=uuid.UUID(version_id),
            )
        )


def test_verifier_scope_is_limited_to_its_allowlisted_workspace(
    stack, actors, verifier_enabled
):
    data = b"another workspace's staged bytes"
    digest = hashlib.sha256(data).hexdigest()
    other_workspace = actors["outsider_workspace_id"]
    other_source, other_version = str(uuid.uuid4()), str(uuid.uuid4())
    other_key = object_key(other_workspace, other_source, other_version)
    assert stack.stage(
        actors["outsider"], other_workspace, other_source, other_version, data
    ).status_code == 201
    assert stack.upload(actors["outsider"], other_key, data).status_code == 200

    verifier = actors["verifier"]
    assert stack.begin(
        verifier, other_workspace, other_source, other_version
    ).status_code in {400, 403, 404}
    assert stack.publish(
        verifier, other_workspace, other_source, other_version,
        digest=digest, size=len(data),
    ).status_code in {400, 403, 404}
    assert stack.read(verifier, other_key).status_code in {400, 401, 403, 404}
    assert stack.catalog_state(verifier, other_version) == []

    mixed = stack.begin(
        verifier, actors["workspace_id"], other_source, other_version
    )
    assert is_rejected(mixed, postgrest_code="P0002")
    assert stack.catalog_state(actors["outsider"], other_version)[0]["state"] == "staged"
