# Background connector sync worker — 2026-11-15

Status: **deployed and production-verified.**

## Scope

- One worker cycle lists connected GitHub connections with the service-role
  PostgREST credential.
- Each connection runs the existing bounded GitHub sync with incremental
  revision skipping and stale-evidence deletion.
- Logs are content-free: connection id, listed, ingested, unchanged, skipped,
  and error status.
- Default interval: 900 seconds; per-connection document limit: 10.

## Production verification

- Migration 53 applied and verified (53 migrations, pending=0).
- Connector worker deployment: `ae542662-7fe7-40e7-bc0f-82f2d6cf815a`.
- First successful cycle: 1 GitHub connection, `listed=50`, `ingested=0`,
  `unchanged=49`, `skipped=1`.
- `connector_connections.last_sync_at` updated and `last_error_category` empty.
- The worker uses the service-role credential and requires no user session or
  MFA.

## Boundary

- GitHub only in this increment.
- Notion still requires a live connect/sync/disconnect smoke.
- Google Drive, Dropbox, and Confluence remain blocked on provider credentials.
- ACL mapping and provider deletion propagation remain.
- The worker holds the service-role credential; it is isolated from the web app
  and has no inbound HTTP listener.
