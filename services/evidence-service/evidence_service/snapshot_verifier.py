"""Compatibility imports for the standalone snapshot verifier core.

The verifier runs as its own process under ``services/snapshot-verifier``.
This module keeps existing service-side unit-test imports working; the public
Evidence Service does not import or expose these operations.
"""

from evidence_layer.snapshot_verifier import (
    DEFAULT_MAX_SNAPSHOT_BYTES,
    DEFAULT_MAX_VERIFY_SECONDS,
    LocalSupabaseVerificationGateway,
    SnapshotBytesReader,
    SnapshotVerificationError,
    SnapshotVerificationService,
    StagedSnapshotClaim,
    SupabaseStorageSnapshotReader,
    VerificationFailure,
    VerificationGateway,
    VerifiedPublication,
    is_valid_verifier_access_token,
    snapshot_object_key,
)

__all__ = [
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "DEFAULT_MAX_VERIFY_SECONDS",
    "LocalSupabaseVerificationGateway",
    "SnapshotBytesReader",
    "SnapshotVerificationError",
    "SnapshotVerificationService",
    "StagedSnapshotClaim",
    "SupabaseStorageSnapshotReader",
    "VerificationFailure",
    "VerificationGateway",
    "VerifiedPublication",
    "is_valid_verifier_access_token",
    "snapshot_object_key",
]
