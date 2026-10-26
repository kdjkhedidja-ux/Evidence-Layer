"""Opt-in live checks of the snapshot Storage policies and scoped verifier publication.

Prepared, NOT yet run against any stack. Runs only against an isolated local
Supabase stack that already has the project migrations applied (private
``source-snapshots`` bucket, catalog tables, the verifier-identity RPCs).
Required local-only environment:

* EVIDENCE_LAYER_LOCAL_STORAGE_URL: loopback API URL
* EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY: publishable/anon API key
* EVIDENCE_LAYER_LOCAL_AUTH_SIGNING_JWK_PATH: the stack's ES256 signing key
  (JWK list), used only to mint tokens for the legacy-role and rogue-key checks.
* EVIDENCE_LAYER_LOCAL_DB_CONTAINER: the stack's ``supabase_db_*`` container,
  used only for operator actions clients cannot perform: adding workspace
  memberships and (un)listing the verifier user on the workspace-scoped
  allowlist.

Publication is closed by default: the allowlist is empty and the legacy
``snapshot_verifier`` role can no longer publish. The verifier is an ordinary
Auth user listed for ONE workspace; a token naming a database role proves who
called, never that an object's SHA-256 was verified. The tests list the user
explicitly on the isolated stack, as an operator would, and unlist it again.

Nothing here touches a hosted project, and no read path is wired to the app.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

BUCKET = "source-snapshots"
_ENV = (
    "EVIDENCE_LAYER_LOCAL_STORAGE_URL",
    "EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY",
    "EVIDENCE_LAYER_LOCAL_AUTH_SIGNING_JWK_PATH",
    "EVIDENCE_LAYER_LOCAL_DB_CONTAINER",
)
_MISSING = [name for name in _ENV if not os.environ.get(name)]

pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason="isolated local Supabase stack settings are unset"
)


class Stack:
    def __init__(self) -> None:
        self.api = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_URL"].rstrip("/")
        parsed = urlsplit(self.api)
        host = parsed.hostname or ""
        if parsed.scheme != "http" or not (
            host == "localhost" or ipaddress.ip_address(host).is_loopback
        ):
            raise ValueError("live policy tests accept loopback URLs only")
        container = os.environ["EVIDENCE_LAYER_LOCAL_DB_CONTAINER"]
        if not container.startswith("supabase_db_"):
            raise ValueError("expected a local supabase_db_* container")
        self.container = container
        self.key = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY"]
        with open(os.environ["EVIDENCE_LAYER_LOCAL_AUTH_SIGNING_JWK_PATH"]) as handle:
            self._jwk = json.load(handle)[0]
        self.client = httpx.Client(timeout=10.0, follow_redirects=False)

    def headers(self, token: str, **extra: str) -> dict[str, str]:
        return {"apikey": self.key, "authorization": f"Bearer {token}", **extra}

    def signup(self, tag: str) -> tuple[str, str]:
        response = self.client.post(
            f"{self.api}/auth/v1/signup",
            headers={"apikey": self.key},
            json={
                "email": f"{tag}-{uuid4().hex[:8]}@example.invalid",
                "password": secrets.token_urlsafe(24),
            },
        )
        response.raise_for_status()
        body = response.json()
        return body["access_token"], body["user"]["id"]

    def personal_workspace(self, token: str) -> str:
        response = self.client.post(
            f"{self.api}/rest/v1/rpc/ensure_personal_workspace",
            headers=self.headers(token, **{"content-type": "application/json"}),
            json={},
        )
        response.raise_for_status()
        return response.json()[0]["workspace_id"]

    def sql(self, statement: str) -> None:
        subprocess.run(
            ["docker", "exec", self.container, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-qc", statement],
            check=True,
            capture_output=True,
        )

    def add_member(self, workspace_id: str, user_id: str) -> None:
        self.sql(
            "insert into public.workspace_memberships (workspace_id, user_id, role, status) "
            f"values ('{workspace_id}', '{user_id}', 'member', 'active')"
        )

    def set_verifier_listed(self, listed: bool, user_id: str, workspace_id: str) -> None:
        self.sql(
            "insert into private.snapshot_verifier_allowlist (user_id, workspace_id) "
            f"values ('{user_id}', '{workspace_id}') on conflict do nothing"
            if listed
            else "delete from private.snapshot_verifier_allowlist "
            f"where user_id = '{user_id}' and workspace_id = '{workspace_id}'"
        )

    def set_legacy_role_membership(self, enabled: bool) -> None:
        # Operator action used only to prove the legacy route stays closed even
        # if the old role were assumable.
        self.sql(
            "grant snapshot_verifier to authenticator"
            if enabled
            else "revoke snapshot_verifier from authenticator"
        )

    def mint(
        self, role: str, *, signing_key=None, kid: str | None = None, sub: str | None = None
    ) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": f"{self.api}/auth/v1",
                "aud": "authenticated",
                "role": role,
                "sub": sub or str(uuid4()),
                "iat": now,
                "exp": now + 300,
            },
            signing_key or jwt.PyJWK(self._jwk).key,
            algorithm="ES256",
            headers={"kid": kid or self._jwk["kid"], "typ": "JWT"},
        )

    # Catalog (PostgREST) -----------------------------------------------------
    def stage(self, token: str, workspace_id: str, source_id: str, version_id: str, data: bytes, **override):
        row = {
            "workspace_id": workspace_id,
            "source_id": source_id,
            "version_id": version_id,
            "object_key": object_key(workspace_id, source_id, version_id),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "state": "staged",
        }
        row.update(override)
        return self.client.post(
            f"{self.api}/rest/v1/source_snapshots",
            headers=self.headers(token, **{"content-type": "application/json", "prefer": "return=minimal"}),
            json=row,
        )

    def begin(self, token: str, workspace_id: str, source_id: str, version_id: str):
        return self.client.post(
            f"{self.api}/rest/v1/rpc/begin_snapshot_verification",
            headers=self.headers(token, **{"content-type": "application/json"}),
            json={"p_workspace_id": workspace_id, "p_source_id": source_id, "p_version_id": version_id},
        )

    def publish(
        self, token: str, workspace_id: str, source_id: str, version_id: str, *, digest: str, size: int
    ):
        """Publish through the scoped RPC, presenting the Storage object identity
        returned by begin. A failing begin is returned as the result."""
        opened = self.begin(token, workspace_id, source_id, version_id)
        if opened.status_code != 200 or len(opened.json()) != 1:
            return opened
        claim = opened.json()[0]
        return self.client.post(
            f"{self.api}/rest/v1/rpc/publish_verified_snapshot",
            headers=self.headers(token, **{"content-type": "application/json"}),
            json={
                "p_workspace_id": workspace_id,
                "p_source_id": source_id,
                "p_version_id": version_id,
                "p_sha256": digest,
                "p_size_bytes": size,
                "p_storage_object_id": claim["out_storage_object_id"],
                "p_storage_object_version": claim["out_storage_object_version"],
            },
        )

    def legacy_patch_publish(self, token: str, version_id: str, *, digest: str, size: int):
        """The pre-RPC route (direct PATCH under the legacy role). Must stay closed."""
        return self.client.patch(
            f"{self.api}/rest/v1/source_snapshots?version_id=eq.{version_id}",
            headers=self.headers(token, **{"content-type": "application/json", "prefer": "return=representation"}),
            json={"state": "published", "verified_sha256": digest, "verified_size_bytes": size},
        )

    def catalog_state(self, token: str, version_id: str) -> list[dict]:
        response = self.client.get(
            f"{self.api}/rest/v1/source_snapshots?select=state,verified_at&version_id=eq.{version_id}",
            headers=self.headers(token),
        )
        response.raise_for_status()
        return response.json()

    def grant(self, owner_token: str, workspace_id: str, source_id: str, principal_id: str):
        return self.client.post(
            f"{self.api}/rest/v1/source_grants",
            headers=self.headers(owner_token, **{"content-type": "application/json", "prefer": "return=minimal"}),
            json={
                "workspace_id": workspace_id,
                "source_id": source_id,
                "principal_id": principal_id,
                "grant_type": "read",
            },
        )

    def revoke(self, owner_token: str, workspace_id: str, source_id: str, principal_id: str):
        return self.client.delete(
            f"{self.api}/rest/v1/source_grants?workspace_id=eq.{workspace_id}"
            f"&source_id=eq.{source_id}&principal_id=eq.{principal_id}",
            headers=self.headers(owner_token),
        )

    # Storage -----------------------------------------------------------------
    def upload(self, token: str, key: str, data: bytes, *, upsert: bool = False):
        return self.client.post(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(
                token, **{"content-type": "application/octet-stream", "x-upsert": "true" if upsert else "false"}
            ),
            content=data,
        )

    def replace(self, token: str, key: str, data: bytes):
        return self.client.put(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(token, **{"content-type": "application/octet-stream"}),
            content=data,
        )

    def read(self, token: str, key: str):
        return self.client.get(f"{self.api}/storage/v1/object/{BUCKET}/{key}", headers=self.headers(token))

    def delete(self, token: str, key: str):
        return self.client.delete(f"{self.api}/storage/v1/object/{BUCKET}/{key}", headers=self.headers(token))


def object_key(workspace_id: str, source_id: str, version_id: str) -> str:
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


@pytest.fixture(scope="module")
def actors(stack: Stack) -> dict:
    owner, owner_id = stack.signup("owner")
    workspace_id = stack.personal_workspace(owner)
    granted, granted_id = stack.signup("granted")
    ungranted, ungranted_id = stack.signup("ungranted")
    outsider, outsider_id = stack.signup("outsider")
    outsider_workspace_id = stack.personal_workspace(outsider)
    verifier, verifier_id = stack.signup("verifier")
    stack.add_member(workspace_id, granted_id)
    stack.add_member(workspace_id, ungranted_id)
    return {
        "owner": owner,
        "owner_id": owner_id,
        "workspace_id": workspace_id,
        "granted": granted,
        "granted_id": granted_id,
        "ungranted": ungranted,
        "outsider": outsider,
        "outsider_workspace_id": outsider_workspace_id,
        "verifier": verifier,
        "verifier_id": verifier_id,
    }


@pytest.fixture
def verifier_enabled(stack: Stack, actors: dict):
    stack.set_verifier_listed(True, actors["verifier_id"], actors["workspace_id"])
    yield
    stack.set_verifier_listed(False, actors["verifier_id"], actors["workspace_id"])


def new_version(stack: Stack, actors: dict, data: bytes, source_id: str | None = None):
    source_id = source_id or str(uuid4())
    version_id = str(uuid4())
    workspace_id = actors["workspace_id"]
    assert stack.stage(actors["owner"], workspace_id, source_id, version_id, data).status_code == 201
    return source_id, version_id, object_key(workspace_id, source_id, version_id)


def test_publication_is_closed_by_default(stack, actors):
    stack.set_verifier_listed(False, actors["verifier_id"], actors["workspace_id"])
    data = b"closed by default"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200

    # The verifier user is an ordinary Auth user. Not listed for the workspace,
    # it cannot begin, publish, see the staged catalog row, or read the object.
    token = actors["verifier"]
    workspace_id = actors["workspace_id"]
    assert stack.begin(token, workspace_id, source_id, version_id).status_code in {401, 403}
    assert stack.publish(
        token, workspace_id, source_id, version_id, digest=digest, size=len(data)
    ).status_code in {401, 403}
    assert stack.catalog_state(token, version_id) == []
    assert stack.read(token, key).status_code in {400, 401, 403, 404}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_legacy_role_route_is_closed_even_if_the_role_were_assumable(stack, actors):
    data = b"legacy route stays closed"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200
    legacy = stack.mint("snapshot_verifier")
    try:
        stack.set_legacy_role_membership(True)
        patched = stack.legacy_patch_publish(legacy, version_id, digest=digest, size=len(data))
        assert patched.status_code in {401, 403}
        assert stack.read(legacy, key).status_code in {400, 401, 403, 404}
    finally:
        stack.set_legacy_role_membership(False)
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_unpublished_object_is_unreadable_and_cannot_be_overwritten_or_deleted(stack, actors, verifier_enabled):
    data = b"staged but never published"
    source_id, version_id, key = new_version(stack, actors, data)
    assert stack.upload(actors["owner"], key, data).status_code == 200

    # The unpublished (orphan-until-published) object is invisible to every client.
    for who in ("owner", "granted", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who
    stack.grant(actors["owner"], actors["workspace_id"], source_id, actors["granted_id"])
    assert stack.read(actors["granted"], key).status_code in {400, 403, 404}

    # Overwrite, upsert, replace and delete are all denied; bytes are unchanged.
    assert stack.upload(actors["owner"], key, b"evil", upsert=True).status_code in {400, 403}
    assert stack.upload(actors["owner"], key, b"evil").status_code in {400, 409}
    assert stack.replace(actors["owner"], key, b"evil").status_code in {400, 403, 404}
    assert stack.delete(actors["owner"], key).status_code in {400, 403}
    stored = stack.read(actors["verifier"], key)
    assert stored.status_code == 200 and stored.content == data
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_upload_requires_a_staged_record_owned_by_the_uploader(stack, actors, verifier_enabled):
    data = b"payload"
    source_id, version_id, key = new_version(stack, actors, data)
    unrecorded = object_key(actors["workspace_id"], str(uuid4()), str(uuid4()))

    assert stack.upload(actors["owner"], unrecorded, data).status_code in {400, 403}
    for who in ("granted", "ungranted", "outsider"):
        assert stack.upload(actors[who], key, data).status_code in {400, 403}, who
    assert stack.upload(actors["verifier"], key, data).status_code in {400, 401, 403}
    assert stack.upload(actors["owner"], key, data).status_code == 200


def test_publication_requires_the_trusted_verifier_and_a_matching_attestation(stack, actors, verifier_enabled):
    data = b"verified snapshot bytes"
    digest = hashlib.sha256(data).hexdigest()
    source_id, version_id, key = new_version(stack, actors, data)
    workspace_id = actors["workspace_id"]

    # Publishing before the object exists is rejected even for the verifier.
    early = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id, digest=digest, size=len(data)
    )
    assert early.status_code in {400, 403}
    assert stack.upload(actors["owner"], key, data).status_code == 200

    # No client path can publish: not the owner, not by inserting as published,
    # not by forging the verification columns on insert. The owner first grants
    # themselves read access so a denial cannot be explained by row visibility.
    assert stack.grant(actors["owner"], workspace_id, source_id, actors["owner_id"]).status_code == 201
    owner_publish = stack.publish(
        actors["owner"], workspace_id, source_id, version_id, digest=digest, size=len(data)
    )
    assert owner_publish.status_code in {401, 403}
    direct = stack.stage(
        actors["owner"], workspace_id, str(uuid4()), str(uuid4()), data, state="published"
    )
    assert direct.status_code in {400, 401, 403}
    forged = stack.stage(
        actors["owner"], workspace_id, str(uuid4()), str(uuid4()), data,
        verified_sha256=digest, verified_size_bytes=len(data),
    )
    assert forged.status_code in {400, 401, 403}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"
    assert stack.revoke(actors["owner"], workspace_id, source_id, actors["owner_id"]).status_code in {200, 204}

    # A verifier attestation that differs from the staged claim is rejected.
    wrong = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id, digest="0" * 64, size=len(data)
    )
    assert wrong.status_code in {400, 403}
    wrong_size = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id, digest=digest, size=len(data) + 1
    )
    assert wrong_size.status_code in {400, 403}

    # A token signed by anything other than the stack's key is not the verifier,
    # even when it names the verifier's own subject.
    rogue = stack.mint(
        "authenticated", signing_key=ec.generate_private_key(ec.SECP256R1()), sub=actors["verifier_id"]
    )
    assert stack.publish(
        rogue, workspace_id, source_id, version_id, digest=digest, size=len(data)
    ).status_code in {401, 403}
    assert stack.read(rogue, key).status_code in {400, 401, 403}

    # The verifier reads the stored bytes, recomputes digest and size, then publishes.
    stored = stack.read(actors["verifier"], key)
    assert stored.status_code == 200
    published = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id,
        digest=hashlib.sha256(stored.content).hexdigest(), size=len(stored.content),
    )
    assert published.status_code in {200, 204}

    # Reading requires membership AND an explicit grant. Ownership alone is not enough.
    for who in ("owner", "granted", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who
    assert stack.grant(actors["owner"], workspace_id, source_id, actors["granted_id"]).status_code == 201
    allowed = stack.read(actors["granted"], key)
    assert allowed.status_code == 200 and allowed.content == data
    assert hashlib.sha256(allowed.content).hexdigest() == digest
    assert stack.catalog_state(actors["granted"], version_id)[0]["verified_at"]
    for who in ("owner", "ungranted", "outsider"):
        assert stack.read(actors[who], key).status_code in {400, 403, 404}, who

    # A published object cannot be replaced, deleted, or re-published.
    assert stack.upload(actors["owner"], key, b"evil", upsert=True).status_code in {400, 403}
    assert stack.upload(actors["owner"], key, b"evil").status_code in {400, 403, 409}
    assert stack.upload(actors["granted"], key, b"evil", upsert=True).status_code in {400, 403}
    assert stack.replace(actors["granted"], key, b"evil").status_code in {400, 403, 404}
    assert stack.delete(actors["owner"], key).status_code in {400, 403}
    assert stack.delete(actors["granted"], key).status_code in {400, 403}
    republish = stack.publish(
        actors["verifier"], workspace_id, source_id, version_id, digest=digest, size=len(data)
    )
    assert republish.status_code in {400, 403}
    assert stack.read(actors["granted"], key).content == data

    # Revoking the grant closes access immediately.
    assert stack.revoke(actors["owner"], workspace_id, source_id, actors["granted_id"]).status_code in {200, 204}
    assert stack.read(actors["granted"], key).status_code in {400, 403, 404}


def test_object_without_a_matching_catalog_record_is_never_publishable(stack, actors, verifier_enabled):
    # Uploaded with a size that differs from the staged claim: publication fails.
    claimed = b"x" * 10
    source_id, version_id, key = new_version(stack, actors, claimed)
    assert stack.upload(actors["owner"], key, b"y" * 11).status_code == 200
    mismatch = stack.publish(
        actors["verifier"], actors["workspace_id"], source_id, version_id,
        digest=hashlib.sha256(claimed).hexdigest(), size=len(claimed),
    )
    assert mismatch.status_code in {400, 403}
    assert stack.catalog_state(actors["owner"], version_id)[0]["state"] == "staged"


def test_database_cannot_detect_a_dishonest_attestation_so_readers_must_reverify(
    stack, actors, verifier_enabled
):
    """Documents a limit: PostgreSQL checks the attestation is consistent with
    the staged claim and the stored size, not that the bytes hash to it."""
    claimed = b"A" * 16
    actual = b"B" * 16  # same size, different content
    source_id, version_id, key = new_version(stack, actors, claimed)
    assert stack.upload(actors["owner"], key, actual).status_code == 200

    # A dishonest verifier simply repeats the claimed digest. The catalog accepts it.
    published = stack.publish(
        actors["verifier"], actors["workspace_id"], source_id, version_id,
        digest=hashlib.sha256(claimed).hexdigest(), size=len(claimed),
    )
    assert published.status_code in {200, 204}

    assert stack.grant(actors["owner"], actors["workspace_id"], source_id, actors["granted_id"]).status_code == 201
    served = stack.read(actors["granted"], key)
    assert served.status_code == 200
    assert stack.catalog_state(actors["granted"], version_id)[0]["state"] == "published"
    # The mismatch is only visible to a reader that recomputes the digest, which
    # is why the secure read path re-verifies SHA-256 before returning bytes.
    assert hashlib.sha256(served.content).hexdigest() != hashlib.sha256(claimed).hexdigest()


def test_verifier_scope_is_limited_to_its_listed_workspace(stack, actors, verifier_enabled):
    """The verifier is listed for the owner's workspace only. It cannot begin,
    publish, read or even see the staged snapshot of another workspace."""
    data = b"another workspace's staged bytes"
    digest = hashlib.sha256(data).hexdigest()
    other_workspace = actors["outsider_workspace_id"]
    other_source, other_version = str(uuid4()), str(uuid4())
    other_key = object_key(other_workspace, other_source, other_version)
    assert stack.stage(actors["outsider"], other_workspace, other_source, other_version, data).status_code == 201
    assert stack.upload(actors["outsider"], other_key, data).status_code == 200

    verifier = actors["verifier"]
    assert stack.begin(verifier, other_workspace, other_source, other_version).status_code in {401, 403}
    assert stack.publish(
        verifier, other_workspace, other_source, other_version, digest=digest, size=len(data)
    ).status_code in {401, 403}
    assert stack.read(verifier, other_key).status_code in {400, 401, 403, 404}
    assert stack.catalog_state(verifier, other_version) == []

    # Naming its own workspace with the other workspace's source and version finds nothing.
    mixed = stack.begin(verifier, actors["workspace_id"], other_source, other_version)
    assert mixed.status_code in {400, 403, 404}
    assert stack.catalog_state(actors["outsider"], other_version)[0]["state"] == "staged"
