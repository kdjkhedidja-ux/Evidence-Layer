"""Hosted Supabase Storage adapter contract: HTTPS-only, user-token reads."""

from __future__ import annotations

import asyncio
import gzip
from uuid import UUID

import httpx
import pytest

from evidence_service.hosted_snapshot_store import HostedSupabaseStorageObjectStore
from evidence_service.secure_snapshot_read import (
    SnapshotObjectUnavailable,
    snapshot_object_key,
)

WORKSPACE_A = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE_A = UUID("c1000000-0000-4000-8000-000000000001")
VERSION_A = UUID("d1000000-0000-4000-8000-000000000001")
TOKEN = "synthetic-hosted-user-token"
CONTENT = b"verified snapshot bytes\n"
ADAPTER_KEY = snapshot_object_key(WORKSPACE_A, SOURCE_A, VERSION_A)
HOSTED_URL = "https://slxfsjnsrvfkdtxayiud.supabase.co"


def _hosted_adapter(handler, **kwargs) -> HostedSupabaseStorageObjectStore:
    return HostedSupabaseStorageObjectStore(
        base_url=HOSTED_URL,
        bucket="source-snapshots",
        publishable_key="sb_publishable_hosted-test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _read(store) -> bytes:
    return asyncio.run(store.read_exact(ADAPTER_KEY, access_token=TOKEN))


def test_hosted_storage_adapter_refuses_http_and_loopback_targets():
    for url in (
        "http://127.0.0.1:55321",
        "http://localhost:55321",
        "https://127.0.0.1",
        "https://localhost",
        "http://example.supabase.co",
        "https://example.supabase.co/path",
        "https://user@example.supabase.co",
        "https://example.supabase.co?x=1",
    ):
        with pytest.raises(ValueError):
            HostedSupabaseStorageObjectStore(
                base_url=url,
                bucket="source-snapshots",
                publishable_key="sb_publishable_hosted-test-key",
            )


def test_hosted_storage_adapter_rejects_service_role_like_api_key():
    with pytest.raises(ValueError):
        HostedSupabaseStorageObjectStore(
            base_url=HOSTED_URL,
            bucket="source-snapshots",
            publishable_key="service_role",
        )


def test_hosted_adapter_reads_over_https_with_user_bearer_and_public_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["key"] = request.headers["apikey"]
        seen["url"] = str(request.url)
        return httpx.Response(200, content=CONTENT)

    assert _read(_hosted_adapter(handler)) == CONTENT
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["key"] == "sb_publishable_hosted-test-key"
    assert seen["url"].startswith(
        "https://slxfsjnsrvfkdtxayiud.supabase.co/storage/v1/object/source-snapshots/"
    )


def test_hosted_adapter_enforces_the_same_streaming_caps():
    def handler(request):
        return httpx.Response(200, content=b"x" * 33)

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_hosted_adapter(handler, max_read_bytes=32))

    def ok_handler(request):
        return httpx.Response(200, content=b"x" * 32)

    assert _read(_hosted_adapter(ok_handler, max_read_bytes=32)) == b"x" * 32


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404, 500])
def test_hosted_adapter_treats_redirects_and_errors_as_unavailable(status):
    def handler(request):
        return httpx.Response(status, content=b"denied")

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_hosted_adapter(handler))


def test_hosted_adapter_refuses_compressed_bodies():
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            content=gzip.compress(b"x" * 64),
        )

    with pytest.raises(SnapshotObjectUnavailable):
        _read(_hosted_adapter(handler))


def test_hosted_adapter_refuses_blank_token_before_any_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, content=CONTENT)

    store = _hosted_adapter(handler)
    with pytest.raises(SnapshotObjectUnavailable):
        asyncio.run(store.read_exact(ADAPTER_KEY, access_token="  "))
    assert called == []


def test_hosted_adapter_rejects_path_traversal_before_http_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, content=CONTENT)

    store = _hosted_adapter(handler)
    with pytest.raises(ValueError):
        asyncio.run(store.read_exact("../../outside", access_token=TOKEN))
    assert called == []
