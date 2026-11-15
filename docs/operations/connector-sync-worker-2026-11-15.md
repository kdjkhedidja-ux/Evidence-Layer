# Background connector sync worker — 2026-11-15

Status: **implemented; deployment pending.**

## Scope

- One worker cycle lists connected GitHub connections with the service-role
  PostgREST credential.
- Each connection runs the existing bounded GitHub sync with incremental
  revision skipping and stale-evidence deletion.
- Logs are content-free: connection id, listed, ingested, unchanged, skipped,
  and error status.
- Default interval: 900 seconds; per-connection document limit: 10.

## Boundary

- GitHub only in this increment.
- Notion still requires a live connect/sync/disconnect smoke.
- Google Drive, Dropbox, and Confluence remain blocked on provider credentials.
- ACL mapping and provider deletion propagation remain.
- The worker holds the service-role credential; it is isolated from the web app
  and has no inbound HTTP listener.
