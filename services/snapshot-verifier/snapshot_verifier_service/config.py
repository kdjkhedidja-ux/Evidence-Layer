"""Runtime configuration for the isolated snapshot verifier command."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from evidence_layer.snapshot_verifier import (
    DEFAULT_MAX_SNAPSHOT_BYTES,
    DEFAULT_MAX_VERIFY_SECONDS,
    LocalSupabaseVerificationGateway,
    SupabaseStorageSnapshotReader,
    is_valid_verifier_access_token,
)

SUPABASE_URL_ENV = "SNAPSHOT_VERIFIER_SUPABASE_URL"
PUBLISHABLE_KEY_ENV = "SNAPSHOT_VERIFIER_PUBLISHABLE_KEY"
ACCESS_TOKEN_ENV = "SNAPSHOT_VERIFIER_ACCESS_TOKEN"


@dataclass(frozen=True)
class VerifierSettings:
    base_url: str
    publishable_key: str
    access_token: str
    max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES
    max_verify_seconds: float = DEFAULT_MAX_VERIFY_SECONDS
    request_timeout_seconds: float = 5.0

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> VerifierSettings:
        values = os.environ if environment is None else environment
        base_url = values.get(SUPABASE_URL_ENV)
        publishable_key = values.get(PUBLISHABLE_KEY_ENV)
        access_token = values.get(ACCESS_TOKEN_ENV)
        if not all(isinstance(item, str) and item for item in (base_url, publishable_key, access_token)):
            raise ValueError("snapshot verifier runtime credentials are missing")
        if not is_valid_verifier_access_token(access_token):
            raise ValueError("snapshot verifier runtime credentials are invalid")

        # Construction validates that the target is loopback-only and the API
        # key is publishable. No clients are opened and no request is sent here.
        LocalSupabaseVerificationGateway(
            base_url=base_url,
            publishable_key=publishable_key,
            timeout_seconds=5.0,
            total_timeout_seconds=DEFAULT_MAX_VERIFY_SECONDS,
        )
        SupabaseStorageSnapshotReader(
            base_url=base_url,
            publishable_key=publishable_key,
            max_snapshot_bytes=DEFAULT_MAX_SNAPSHOT_BYTES,
            timeout_seconds=5.0,
            total_timeout_seconds=DEFAULT_MAX_VERIFY_SECONDS,
        )
        return cls(
            base_url=base_url,
            publishable_key=publishable_key,
            access_token=access_token,
        )
