# Backup B2 result — 2026-11-15

Status: **B2 upload PASS; disposable restore drill PASS.**
No production migration, restore, or worker deployment was performed.

## What changed from the failed attempt

The updated application key is valid and grants the bucket **`evidence2`**, not
the stale `EvidenceLayer` name in the previous environment value. B2 S3 signing
still rejects the key ID as malformed, but the native B2 API authorizes and
uploads correctly. The guard therefore accepts the reviewed B2 target
`evidence2`; the object remains accessible through the reviewed S3 endpoint.

## Artifact

| Item | Value |
|---|---|
| Provider | Backblaze B2 |
| Bucket | `evidence2` |
| Object key | `dumps/2026-11-15/evidence-layer.dump` |
| Endpoint | `https://s3.eu-central-003.backblazeb2.com` |
| SHA-256 | `61df647210bef7d34ff082ffa23c3e892043d15d2949f8a450fd3f2d6b43db6a` |
| Size | 768,160 bytes |
| Uploaded at | 2026-11-15T00:08:06Z |
| Checksum object | `dumps/2026-11-15/evidence-layer.dump.sha256` |

The bucket is private: an unauthenticated S3 `GET` returned HTTP 401.

## Drill result

- Disposable PostgreSQL 17 (non-prod), production non-system roles plus
  `auth`/`extensions`/`storage` stubs.
- Restored counts match production: 13 workspaces, 56 evidence records,
  51 deletion requests, 529 audit rows, 13 memberships, 18 Auth FKs, 98 policies.
- `python -m evidence_service.restore_reconciliation`:
  `status=clean`, `requests_checked=42`, `failures=[]`.
- Evidence: `docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-15.md`.

## Residual note

A 10-byte preflight object `_smoke/write-test.txt` remains in `evidence2`; the
current key can write but cannot list or delete. Remove it with a key that has
`listFiles` and `deleteFiles`, or from the B2 console.

The recovery attestation now points at the B2 artifact. The local dump and
disposable restore container were deleted after the drill.
