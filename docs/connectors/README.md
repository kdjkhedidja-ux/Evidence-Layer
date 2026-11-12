# Connector SDK — first read-only slice

Personal-scope decision: see [`personal-connector-scope.md`](personal-connector-scope.md). The first production path is personal-only; no shared/group document ingestion.

Status: **Notion personal OAuth/manual-sync and GitHub App installation sync are deployed; Google Drive remains Coming soon.** Migrations 41 and 42 are applied, the Evidence Service connector routes are live, and the Notion/GitHub web flags are enabled. A live Notion connect/sync/disconnect smoke is still required. Google Drive is gated off pending Google OAuth verification. The slice includes OAuth connect/callback, app-level encrypted token storage, a manual bounded sync, connector-document storage, and a minimal Connect/Sync UI. Migration `20261111190000` is applied in production for the connector tables. Background scheduling, ACL mapping, source-snapshot verification, deletion propagation, and production rollout are not yet implemented.

Implemented providers:

| Provider | Listing | Fetch | Auth model in this slice | Default limit |
|---|---|---|---|---|
| Google Drive | Drive v3 `files.list`, page tokens | `files.get` / `files.export` for Google-native docs | injected OAuth access token | 8 MiB |
| GitHub | Git Trees API for one repository/ref | Contents API raw bytes | injected GitHub App/fine-grained token; public repos can omit token | 2 MiB |
| Dropbox | `files/list_folder` + continuation cursor | `files/download` | injected OAuth access token | 2 MiB |
| Confluence | REST `content` pages, offset cursor | page `body.storage` HTML | injected Atlassian OAuth token | 8 MiB |
| Notion | Search or database query cursor | page block tree rendered to text | public OAuth connection (page picker); adapter also accepts an integration token | 2,000 blocks |

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

Verification package: [`google-oauth-verification-package.md`](google-oauth-verification-package.md).

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

- The production path uses a **public OAuth connection** with Notion's page picker; each user authorizes their own workspace/pages. An internal integration token remains useful for adapter tests, but must not be used as a shared multi-user credential.
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

## Implemented Google Drive vertical slice

- OAuth start/callback: `/api/connectors/google-drive/start` and `/api/connectors/google-drive/callback` in the Next.js BFF.
- Scope requested: `https://www.googleapis.com/auth/drive.readonly`; offline access and consent are requested to obtain a refresh token.
- Token vault: service-side AES-256-GCM encryption with `CONNECTOR_TOKEN_ENCRYPTION_KEY` (base64 32-byte key). Only ciphertext is stored.
- Tables: `connector_connections` and `connector_documents`, forced RLS, owner-only policies, no anon/service-role table grants.
- Manual sync: `POST /v1/connectors/{connection_id}/sync?limit=10` (maximum 25); one provider page per call, up to 2 MiB per document.
- Content: text-like documents stored in `content_text`; binary content stored as base64 with a 2 MiB cap. No write-back to Drive.
- UI: the workspace page shows Connected sources, Connect, connection status, Sync, and Disconnect.
- Refresh: service refreshes an expired Drive access token using the stored refresh token and server-only Google OAuth client credentials.
- Disconnect: deletes the connection; `connector_documents` cascade. It does not revoke the Google grant in this slice.

Environment names only:

```text
CONNECTOR_TOKEN_ENCRYPTION_KEY   # base64 32-byte key, service only
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI        # web; defaults to <origin>/api/connectors/google-drive/callback
```

Google Cloud console: create a Web application OAuth client, add the exact callback URI, and keep the client secret server-side. No secret belongs in the repository or browser bundle.

## Implemented Notion vertical slice

- Public Notion OAuth connection with the page picker; no Marketplace listing required for OAuth use.
- OAuth start/callback: `/api/connectors/notion/start` and `/api/connectors/notion/callback`.
- `NOTION_REDIRECT_URI` must be the app callback (for example
  `https://evidencelayer.dev/api/connectors/notion/callback`), not Notion's
  authorization endpoint `https://api.notion.com/v1/oauth/authorize`. The route
  rejects a non-callback value.
- Token exchange is server-side with `NOTION_OAUTH_CLIENT_ID` / `NOTION_OAUTH_CLIENT_SECRET`; tokens are encrypted in the same vault as Drive.
- Manual sync: `POST /v1/connectors/{connection_id}/sync` dispatches to Notion when the connection provider is `notion`.
- Pages are rendered from the block tree to text; API version defaults to `2026-03-11`.
- Personal-only UI: `NEXT_PUBLIC_NOTION_CONNECTOR_ENABLED` is enabled in production after the 2026-11-11 connector deployment; a live OAuth smoke remains pending. Google Drive remains gated off and shows “Coming soon”.
- Migration 41 already supports the `notion` provider; no additional schema migration is required.

## GitHub App vertical slice (deployed)

- Auth: **GitHub App installation**, not a broad OAuth `repo` token. The installation token is minted server-side and never stored.
- Required App permissions: **Contents: read**, **Metadata: read**. Installation scope can be all repositories or selected repositories.
- Setup URL: `https://evidencelayer.dev/api/connectors/github/callback`.
- Start URL: `/api/connectors/github/start` redirects to the App's installation page with a state cookie.
- The service stores `provider_config={"installation_id": ...}`; no user access token is persisted.
- Sync lists the installation's repositories and ingests bounded README/code files from selected repositories (up to 5 repositories, 25 documents, 2 MiB per file).
- UI is enabled by `NEXT_PUBLIC_GITHUB_CONNECTOR_ENABLED` in production; empty repositories and unavailable trees are skipped without failing the sync.

Environment names only:

```text
GITHUB_APP_SLUG             # web; installation URL slug (alias: GIT_APP_SLUG)
GITHUB_APP_ID               # service; App JWT issuer (alias: GIT_APP_ID)
GITHUB_APP_PRIVATE_KEY      # service; full PEM private key (alias: GIT_APP_PRIVATE_KEY)
NEXT_PUBLIC_GITHUB_CONNECTOR_ENABLED
```

GitHub App setup is complete for the current production App. Empty repositories are skipped; install on a non-empty repository to ingest README/code documents. No Marketplace listing is required.

## Required next layer (not implemented)

1. **OAuth applications + callback routes** for Dropbox, Atlassian, and GitHub; Google verification and Notion Marketplace listing are separate optional tracks.
2. **Token vault**: encrypted per-user/per-workspace tokens, refresh rotation, revocation, no browser exposure.
3. **Connector selection UI** and explicit folder/site/page/repo selection.
4. **Sync worker** with delta cursors, idempotency, backoff, per-connector health, and deletion/tombstone propagation.
5. **Ingestion** into normalized source snapshots with checksums, MIME extraction, deduplication, and provenance.
6. **ACL mapping** from provider permissions to workspace access; fail closed on unknown ACLs.
7. **Rate-limit and size budgets** per provider and per workspace.
8. **Disconnect + purge** path tied to the existing deletion/account lifecycle.

Until those exist, these adapters are an SDK slice, not a production connector product.
