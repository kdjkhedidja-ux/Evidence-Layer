# Connector SDK — first read-only slice

Status: **read-only connector adapters implemented; OAuth acquisition, encrypted token storage, sync workers, database ingestion, and UI are not yet implemented.** This is the contract layer for the five low-complexity connectors selected for the first wave.

Implemented providers:

| Provider | Listing | Fetch | Auth model in this slice | Default limit |
|---|---|---|---|---|
| Google Drive | Drive v3 `files.list`, page tokens | `files.get` / `files.export` for Google-native docs | injected OAuth access token | 8 MiB |
| GitHub | Git Trees API for one repository/ref | Contents API raw bytes | injected GitHub App/fine-grained token; public repos can omit token | 2 MiB |
| Dropbox | `files/list_folder` + continuation cursor | `files/download` | injected OAuth access token | 2 MiB |
| Confluence | REST `content` pages, offset cursor | page `body.storage` HTML | injected Atlassian OAuth token | 8 MiB |
| Notion | Search or database query cursor | page block tree rendered to text | injected Notion integration token | 2,000 blocks |

Package: `src/evidence_layer/connectors/`. Tests: `tests/test_connectors.py`. Install the connector extra with `pip install -r requirements-connectors.txt`.

## Contract

Every adapter implements:

```python
class EvidenceConnector(Protocol):
    provider: str
    async def list_items(self, *, cursor: str | None = None, limit: int = 100) -> ConnectorPage: ...
    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument: ...
```

Normalized fields:

- `ConnectorItem`: provider, external ID, name, kind, MIME type, revision, updated time, URL, parent, size, metadata.
- `ConnectorPage`: items + next cursor.
- `ConnectorDocument`: item, bytes, content type, revision, SHA-256.

Adapters do not write to providers, do not persist tokens, and do not log authorization headers.

## Provider notes

### Google Drive

- Recommended scope: `https://www.googleapis.com/auth/drive.readonly`.
- Lists `files` with `trashed = false`; supports shared drives via `supportsAllDrives` and optional `driveId`.
- Google-native Docs/Sheets/Slides are exported as HTML/CSV/PDF by default; override `export_mime_types`.
- Binary files use `alt=media`.
- Watch: Google OAuth verification for sensitive scopes, shared-drive permissions, export format limits.

### GitHub

- One connector instance covers one owner/repo/ref. Prefer a GitHub App installation token with contents read permission; a fine-grained token is acceptable for the first slice.
- Lists blobs recursively from the Git Trees API and fetches raw content through the Contents API.
- GitHub tree listing is not paginated by the adapter; it returns the first `limit` eligible files. Add pagination before large-repository rollout.
- Watch: API rate limits, large files (skipped above `max_file_bytes`), binary/large asset handling.

### Dropbox

- Recommended scopes: `files.metadata.read`, `files.content.read`.
- Uses `list_folder`/`list_folder/continue` and `files/download`.
- Watch: shared-folder IDs, path case behavior, rate limits.

### Confluence

- Use Atlassian OAuth 2.0 (3LO); run against `https://api.atlassian.com/ex/confluence/{cloudId}/wiki` or a site base URL. Recommended read scopes include `read:content:confluence` and `read:space:confluence`.
- Lists pages, optionally filtered by space or CQL; fetches `body.storage` HTML.
- Watch: cloud-id/site mapping, page hierarchies, macro-rendered content, attachments (not included yet).

### Notion

- Simplest auth is an internal integration token; the user must explicitly share pages/databases with the integration. Public OAuth is possible later.
- Lists via Search or a database query; fetches block children recursively to depth 2 and caps total blocks.
- Renders rich text to plain text; page properties beyond the title are not yet exported.
- Watch: explicit page sharing, API version changes, block pagination/rate limits.

## Why these five first

They share a simple read-only HTTP model and avoid the first-wave complexity of Slack/Teams/email:

- file/page repositories with stable IDs and revision signals;
- predictable pagination/cursors;
- no write-back requirement;
- append-only ingestion can be validated before building broad connector administration.

Dropbox and Notion are fast to integrate; GitHub is fast for technical teams; Confluence needs Atlassian OAuth/cloud-id work; Google Drive has the broadest user reach but the most OAuth verification/export nuance.

## Required next layer (not implemented)

1. **OAuth applications + callback routes** for Google, Dropbox, Atlassian, Notion; GitHub App installation flow.
2. **Token vault**: encrypted per-user/per-workspace tokens, refresh rotation, revocation, no browser exposure.
3. **Connector selection UI** and explicit folder/site/page/repo selection.
4. **Sync worker** with delta cursors, idempotency, backoff, per-connector health, and deletion/tombstone propagation.
5. **Ingestion** into normalized source snapshots with checksums, MIME extraction, deduplication, and provenance.
6. **ACL mapping** from provider permissions to workspace access; fail closed on unknown ACLs.
7. **Rate-limit and size budgets** per provider and per workspace.
8. **Disconnect + purge** path tied to the existing deletion/account lifecycle.

Until those exist, these adapters are an SDK slice, not a production connector product.
