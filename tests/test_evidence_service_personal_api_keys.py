from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.personal_api_key_repository import (
    SupabasePersonalApiKeyRepository,
    gateway_signature,
    parse_personal_api_key,
)

WORKSPACE = uuid.UUID("22222222-2222-4222-8222-222222222222")
KEY_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
SECRET = "ab" * 32


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
        personal_api_key_gateway_secret=SECRET,
    )


def test_parse_personal_api_key_accepts_only_the_expected_shape() -> None:
    token = f"evl_pk_{KEY_ID}.{'A' * 43}"
    assert parse_personal_api_key(token) == (
        KEY_ID,
        hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )
    assert parse_personal_api_key("jwt.like.token") is None
    assert parse_personal_api_key(f"evl_pk_not-a-uuid.{'A' * 43}") is None
    assert parse_personal_api_key(f"evl_pk_{KEY_ID}.short") is None


def test_create_key_sends_hash_header_and_returns_the_one_time_token() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["hash"] = request.headers["x-evidence-api-key-hash"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=[{
                "id": json.loads(request.content)["p_id"],
                "workspace_id": str(WORKSPACE),
                "name": "Reporting export",
                "key_prefix": "evl_pk_66666666",
                "scopes": ["evidence:read", "relationships:read"],
                "created_at": "2026-11-10T10:00:00Z",
                "expires_at": "2026-12-10T10:00:00Z",
            }],
        )

    repository = SupabasePersonalApiKeyRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    key, token = asyncio.run(
        repository.create_key(
            "verified.jwt", workspace_id=WORKSPACE, name="Reporting export", expiration_days=30
        )
    )

    assert captured["path"] == "/rest/v1/rpc/create_personal_api_key"
    created_id = uuid.UUID(str(captured["body"]["p_id"]))
    assert captured["hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert re.fullmatch(rf"evl_pk_{created_id}\.[A-Za-z0-9_-]{{43}}", token)
    assert captured["body"] == {
        "p_id": str(created_id),
        "p_workspace_id": str(WORKSPACE),
        "p_name": "Reporting export",
        "p_expiration_days": 30,
    }
    assert key.id == created_id


def test_list_and_revoke_use_the_user_token_on_the_data_api() -> None:
    observed: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(
            (request.method, request.url.path, request.headers.get("Authorization"))
        )
        if request.url.path.endswith("/rpc/revoke_personal_api_key"):
            assert json.loads(request.content) == {"p_id": str(KEY_ID)}
            return httpx.Response(200, json=True)
        return httpx.Response(
            200,
            json=[{
                "id": str(KEY_ID),
                "workspace_id": str(WORKSPACE),
                "name": "Reporting export",
                "key_prefix": "evl_pk_66666666",
                "scopes": ["evidence:read", "relationships:read"],
                "created_at": "2026-11-10T10:00:00Z",
                "expires_at": "2026-12-10T10:00:00Z",
                "revoked_at": None,
                "last_used_at": None,
            }],
        )

    repository = SupabasePersonalApiKeyRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    keys = asyncio.run(repository.list_keys("verified.jwt", uuid.UUID(int=1)))
    assert len(keys) == 1 and keys[0].last_used_at is None
    assert asyncio.run(repository.revoke_key("verified.jwt", KEY_ID)) is True
    assert [method for method, _, _ in observed] == ["GET", "POST"]
    assert all(auth == "Bearer verified.jwt" for _, _, auth in observed)


def test_reading_evidence_signs_one_use_gateway_proof_and_pages() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({key: value for key, value in request.headers.items()})
        payload = json.loads(request.content)
        expected = gateway_signature(
            SECRET,
            resource="evidence",
            key_id=KEY_ID,
            workspace_id=WORKSPACE,
            key_hash=captured["x-evidence-api-key-hash"],
            timestamp=int(captured["x-evidence-api-key-gateway-timestamp"]),
            nonce=uuid.UUID(captured["x-evidence-api-key-gateway-nonce"]),
            limit=2,
            cursor_created_at=None,
            cursor_id=None,
        )
        assert captured["x-evidence-api-key-gateway-proof"] == expected
        assert payload["p_limit"] == 2
        return httpx.Response(
            200,
            json=[
                {
                    "id": "33333333-3333-4333-8333-333333333331",
                    "workspace_id": str(WORKSPACE),
                    "content": "alpha",
                    "source_label": None,
                    "source_id": None,
                    "source_version_id": None,
                    "created_at": "2026-11-10T10:00:00Z",
                    "updated_at": "2026-11-10T10:00:00Z",
                },
                {
                    "id": "33333333-3333-4333-8333-333333333332",
                    "workspace_id": str(WORKSPACE),
                    "content": "beta",
                    "source_label": None,
                    "source_id": None,
                    "source_version_id": None,
                    "created_at": "2026-11-10T09:00:00Z",
                    "updated_at": "2026-11-10T09:00:00Z",
                },
            ],
        )

    repository = SupabasePersonalApiKeyRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    page = asyncio.run(
        repository.read_evidence(
            key_id=KEY_ID,
            key_hash="a" * 64,
            workspace_id=WORKSPACE,
            limit=1,
            cursor_created_at=None,
            cursor_id=None,
        )
    )
    assert [record.content for record in page.items] == ["alpha"]
    assert page.next_cursor is not None
    assert int(captured["x-evidence-api-key-gateway-timestamp"]) <= int(time.time())
