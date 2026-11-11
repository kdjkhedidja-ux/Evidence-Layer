# Account export and mediated download (narrowed Phase 8)

Status: **deployed to production and smoke-tested on 2026-11-11.**
Scope: account and personal-workspace export only. Shared/group workspace export is deferred with C3 (see `docs/PHASE8-SCOPE-DECISIONS-2026-11-11.md`).

## What the export contains

The ZIP contains:

- `account.json` — account type/organization name for the caller;
- `workspaces/<workspace-id>/workspace.json` — personal workspace metadata;
- `workspaces/<workspace-id>/evidence.json` — evidence records readable by the caller;
- `workspaces/<workspace-id>/relationships.json` — relationships readable by the caller;
- `workspaces/<workspace-id>/sources.json` — source-catalogue metadata, not source bytes;
- `manifest.json` — `evidence-layer-export-v1`, scope, counts, file sizes/SHA-256, limits;
- `checksums.sha256` — one SHA-256 line per exported data file.

Only workspaces with `personal_owner_id = caller` are queried. Group/shared workspaces are never included. The export does not include Auth email, passwords, tokens, source bytes, or other members' private data.

## Storage and authorization

- The ZIP is stored as base64 in forced-RLS tables (`public.data_export_jobs`, `public.data_export_grants`); clients have no table privileges.
- No public bucket and no direct Storage URL are used. This avoids adding a `service_role` credential to the user-facing service.
- Maximum artifact size: **8 MiB**. Maximum rows per resource: **5,000**; exceeding either fails with `413`.
- Creation, redemption, revocation, and cleanup go through security-definer RPCs that derive the caller from `auth.uid()`.
- Creation and redemption fail closed when `account_lifecycle.state <> 'available'`.

## TTL, grant, and revocation

- Export expiry: **24 hours after ready**.
- Download grant: requester-bound, **single-use**, expires no later than the export (maximum 24 hours).
- Download response: `Cache-Control: private, no-store`, `Content-Type: application/zip`, `Content-Disposition`, and `Content-Digest`.
- `POST /v1/me/exports/{id}/revoke` clears the artifact, marks the export revoked, and invalidates the grant immediately.
- Expired exports/grants are cleared by `private.cleanup_expired_exports()`, called from the continuous deletion worker each cycle and available through `export_cleanup_worker.py` for operator/maintenance use.
- Already-downloaded client copies cannot be recalled.

## API path

Service endpoints (behind the normal authenticated BFF):

```text
POST /v1/me/exports
  body: {"idempotency_key":"..."}
  201: {export_id, status, ready_at, expires_at, reused, grant_token, download_path}

GET /v1/me/exports/{export_id}/download
  header: X-Export-Grant: <single-use token>
  200: application/zip

POST /v1/me/exports/{export_id}/revoke
  200: {export_id, revoked}
```

The web UI is gated by `NEXT_PUBLIC_ACCOUNT_EXPORT_ENABLED`; the default is off
unless the value is exactly `true`. BFF routes:

```text
POST /api/evidence/me/exports
GET  /api/evidence/me/exports/{exportId}/download   (X-Export-Grant header)
POST /api/evidence/me/exports/{exportId}/revoke
```

A repeated create with the same idempotency key returns the existing job with `reused=true` and no new grant token. Request a new export with a new key for a new download.

## Failure semantics

| Condition | Result |
|---|---|
| Account lifecycle not available | `403`; no export or grant is issued |
| Export exceeds size/row limit | `413`; no job is inserted |
| Invalid, expired, redeemed, or revoked grant | `404`; no bytes are emitted |
| Export service/Data API unavailable | `503`; retry is safe |
| Revoked/expired export | artifact cleared; no new grant without a new export |

## Local verification

- pgTAP `account_export_grants.test.sql`: finalize, single-use redemption, cross-user denial, revoke, cleanup.
- Full local pgTAP suite: 21 files, 589 tests pass.
- Python repository/route/worker/guard tests pass; migration guard: 40 files, 713 statements, 612 approved, 0 hard-denied.
- Web typecheck passes; 29 files, 196 Vitest tests pass.
- C8 deletion-worker smoke on a dedicated test account: request `8de519e1-69ad-48d3-a2bf-d2959958d985` reached `purged`, protected access returned 403, Auth lookup returned 404.
- Production export smoke: export `02526f9e-4b11-41c4-91cf-6d9e57da3fd4` downloaded once with `private, no-store` and a valid ZIP; second download 404. Revoked export `d98bb0a9-07d1-4093-83f3-90debde15aa1` cleared the artifact and its download returned 404. Test account purge request `52d4c0ae-267d-437a-b67f-8226cd540ef9` reached `purged`, Auth 404.

## Production deployment status and residual risks

- Migration `20261111180000_phase8_account_export.sql` is applied (40 migrations in production).
- Evidence Service deployment `44fbb6f2-0e3a-407a-8e20-fc6b6e5b18e6` is live with the export routes (1 replica).
- Worker deployment `eeb6449e-7a2d-41f8-bb94-4f8b254fea87` is live with the export-cleanup hook (1 replica); the cleanup function is being called.
- Vercel production deployment `dpl_DAbgSJRRs3QbUPxA3ka3ttHQBfrq` is READY; `NEXT_PUBLIC_ACCOUNT_EXPORT_ENABLED=true`.
- Shared/group workspace export is not available.
- The export artifact is a database row, not a separate export bucket; large exports are rejected rather than streamed.
- Already-downloaded client copies cannot be recalled.
- Expiry cleanup depends on the continuous worker; if the worker is unhealthy, artifacts remain until cleanup resumes.
- No full provider backup/PITR absence proof is claimed.
