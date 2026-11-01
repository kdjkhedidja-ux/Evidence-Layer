"""Hosted Supabase Storage object store for the verified snapshot read path.

``secure_snapshot_read.py`` is a pinned input of the frozen Phase 7 benchmark
manifest, so this HTTPS adapter lives in its own module and subclasses the
local adapter to reuse its bounded request machinery without editing that
file. The only difference is the endpoint contract: where
``LocalSupabaseStorageObjectStore`` requires plain HTTP on a loopback host
(the disposable local stack), this adapter requires HTTPS on a non-loopback
host (a hosted Supabase project).

Object access is authorized by Supabase Storage RLS under the request user's
bearer token; the adapter adds no privilege of its own and, like the local
adapter, refuses service-role-shaped API keys, redirects, compressed bodies,
and oversize reads.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx

from evidence_service.secure_snapshot_read import (
    _ABSOLUTE_MAX_READ_BYTES,
    _is_public_api_key,
    _valid_seconds,
    DEFAULT_MAX_SNAPSHOT_BYTES,
    DEFAULT_STORAGE_TOTAL_TIMEOUT_SECONDS,
    LocalSupabaseStorageObjectStore,
)


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname or "").is_loopback
    except ValueError:
        return False


class HostedSupabaseStorageObjectStore(LocalSupabaseStorageObjectStore):
    """Supabase Storage adapter restricted to HTTPS, non-loopback endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        bucket: str,
        publishable_key: str,
        timeout_seconds: float = 5.0,
        total_timeout_seconds: float = DEFAULT_STORAGE_TOTAL_TIMEOUT_SECONDS,
        max_read_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or _is_loopback_host(parsed.hostname)
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not bucket
            or "/" in bucket
            or not _is_public_api_key(publishable_key)
            or not 0 < timeout_seconds <= 30
            or not _valid_seconds(total_timeout_seconds)
            or type(max_read_bytes) is not int
            or not 0 < max_read_bytes <= _ABSOLUTE_MAX_READ_BYTES
        ):
            raise ValueError("hosted Storage configuration is invalid")
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket
        self._publishable_key = publishable_key
        self._timeout = timeout_seconds
        self._total_timeout = float(total_timeout_seconds)
        self._max_read_bytes = max_read_bytes
        self._client = client
