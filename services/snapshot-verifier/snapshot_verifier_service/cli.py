"""One-shot CLI. It accepts snapshot identifiers and never runs an HTTP app."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from evidence_layer.snapshot_verifier import (
    LocalSupabaseVerificationGateway,
    SnapshotVerificationError,
    SnapshotVerificationService,
    SupabaseStorageSnapshotReader,
    VerifiedPublication,
)

from .config import VerifierSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snapshot-verifier")
    parser.add_argument("--workspace-id", required=True, type=UUID)
    parser.add_argument("--source-id", required=True, type=UUID)
    parser.add_argument("--version-id", required=True, type=UUID)
    return parser


async def _verify(
    settings: VerifierSettings,
    workspace_id: UUID,
    source_id: UUID,
    version_id: UUID,
) -> VerifiedPublication:
    gateway = LocalSupabaseVerificationGateway(
        base_url=settings.base_url,
        publishable_key=settings.publishable_key,
        timeout_seconds=settings.request_timeout_seconds,
        total_timeout_seconds=settings.max_verify_seconds,
    )
    reader = SupabaseStorageSnapshotReader(
        base_url=settings.base_url,
        publishable_key=settings.publishable_key,
        max_snapshot_bytes=settings.max_snapshot_bytes,
        timeout_seconds=settings.request_timeout_seconds,
        total_timeout_seconds=settings.max_verify_seconds,
    )
    service = SnapshotVerificationService(
        gateway=gateway,
        reader=reader,
        max_snapshot_bytes=settings.max_snapshot_bytes,
        max_verify_seconds=settings.max_verify_seconds,
    )
    return await service.verify_and_publish(
        settings.access_token,
        workspace_id=workspace_id,
        source_id=source_id,
        version_id=version_id,
    )


def main(
    argv: list[str] | None = None,
    *,
    environment: dict[str, str] | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        settings = VerifierSettings.from_environment(environment)
    except (TypeError, ValueError):
        print("snapshot verifier configuration invalid", file=sys.stderr)
        return 2

    try:
        result = asyncio.run(
            _verify(
                settings,
                arguments.workspace_id,
                arguments.source_id,
                arguments.version_id,
            )
        )
    except SnapshotVerificationError as error:
        print(f"snapshot verification failed: {error.reason.value}", file=sys.stderr)
        return 1
    except Exception:
        # Do not print transport details, tokens, object keys, or response bodies.
        print("snapshot verification failed: internal_error", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "workspace_id": str(result.workspace_id),
                "source_id": str(result.source_id),
                "version_id": str(result.version_id),
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
            },
            sort_keys=True,
        )
    )
    return 0
