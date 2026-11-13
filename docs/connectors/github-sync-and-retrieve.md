# GitHub sync and retrieve bridge

Status: implemented for the personal connector path. Migration `20261112140000` adds the personal-workspace bridge.

## Scope

- Personal scope only. GitHub documents are bridged into the user's **personal workspace** where the user has the `owner` role.
- Group workspaces are not used for connector evidence because the current evidence write policy requires the workspace owner role.
- GitHub remains read-only; no write-back to repositories.

## Ingest policy

Current caps:

| Cap | Value |
|---|---:|
| Repositories per sync | 5 |
| Documents per sync | 100 |
| Bytes per document | 2 MiB |
| Total content bytes per sync | 12 MiB |
| Bridged evidence per document | 65,536 characters |

Priority order:

1. root `README*`
2. `docs/**`
3. `*.md`, `*.mdx`, `*.rst`, `*.txt`
4. secondary code/config text: `.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.go`, `.rs`, `.java`, `.kt`, `.rb`, `.php`, `.c`, `.h`, `.cpp`, `.cs`, `.swift`, `.sh`, `.json`, `.yml`, `.yaml`, `.toml`, `.ini`, `.cfg`
5. everything else is skipped

Excluded: `node_modules`, `vendor`, `dist`, `build`, `.next`, `coverage`, `.git`, `__pycache__`, lockfiles, minified files, source maps, binaries, and oversized files.

Path ordering is deterministic; the tree API is read once per repository and the bounded candidate set is sorted before fetch.

## Bridge behavior

For every synced text document:

- normalize text to UTF-8 `content_text`;
- upsert `connector_documents` by `(connection_id, external_id)`;
- upsert `evidence_records` by `(connector_connection_id, connector_external_id)`;
- provenance label: `GitHub: owner/repo/path`;
- store connector revision (Git blob SHA) for change detection.

The existing `POST /v1/personal-api-keys/workspaces/{workspace_id}/retrieve` then sees the bridged evidence without synthetic seeding.

Evidence writes use the authenticated user and existing evidence RLS; no service-role user path is introduced.

## Re-sync and durability

- Re-sync replaces changed content for the same external id and updates the evidence body/revision.
- After an error-free sync, evidence for paths no longer returned by the repository is deleted.
- If a repository tree or document fetch fails, the connection stays `connected`, the sync is marked `partial`, and old evidence is retained instead of being deleted. A later successful sync reconciles stale rows.
- Empty repositories are skipped without failing the whole installation.
- `last_sync_at` and `last_error_category` are updated through the existing connection status fields.
- Disconnect deletes the connector connection; the evidence FK cascades, removing bridged evidence. Account/workspace deletion follows the existing deletion path.

## Operator steps

1. Apply migration `20261112140000_connector_evidence_bridge.sql`.
2. Deploy the Evidence Service.
3. In workspace Connected sources, click **Sync** again. Existing GitHub connections are backfilled to the user's personal workspace; a sync rebuilds text and evidence.
4. Use the **personal workspace ID** shown in Authorized workspace for the API key/retrieve/MCP configuration. The group workspace may still show zero evidence because the bridge is personal-only.
5. Confirm retrieve returns documents for the personal workspace.

## Limits

- Only text documents are bridged; binary files remain in `connector_documents` as base64 and are not evidence.
- The 100-document cap is not a full repository clone.
- Selection is lexical; no JEV.
- Only the current sync set is guaranteed; partial failures retain old evidence until a successful re-sync.
- Group/shared workspace bridging is out of scope.
