# P7 Staging Preflight: 2026-11-05

This record captures the read-only staging preflight performed with the temporary Supabase Management API credential supplied for the test project. The credential was not written to the repository, logs, or documentation.

## Target

- Project ref: `wqtubmirrcotwqkcvugh`
- Project URL: `https://wqtubmirrcotwqkcvugh.supabase.co`
- Project name: `preproduction-migration-gate`
- Reported status: `ACTIVE_HEALTHY`
- Region: `eu-central-1`

## Read-only checks

- Management API authentication: **PASS** (`GET /v1/projects/{ref}` returned HTTP 200).
- Direct Data API authentication with the supplied Management API token: **NOT APPLICABLE** (`/rest/v1/` and `/auth/v1/user` returned HTTP 401). The token is a management credential, not an end-user Supabase JWT/API key.
- Remote SQL query endpoint: **PASS** for read-only `SELECT` queries.
- Remote database identity: `postgres` database, `public` schema.
- Remote migration history: **14 applied migrations**.
- Local reviewed migration inventory: **21 files**, **411 statements**, **338 registry-approved**, **0 hard-denied** (`scripts/supabase_migration_guard.py validate/classify`).
- Remote lifecycle/request/worker schema check: `account_lifecycle`, `data_deletion_requests`, and the worker-role placeholder are absent from the remote database at this preflight point.
- Remote recovery metadata: PITR is disabled, `backup_count=0`, and `physical_backup_count=0`; no usable recovery point was verified.

## Result

**STAGING APPLY BLOCKED.** The target is healthy and reachable, but its migration history is behind the reviewed local inventory by seven migrations and the required recovery-point gate is unsatisfied. No migration, worker activation, purge, or data mutation was performed. The exact migration plan/apply path must be reviewed and explicitly authorized before changing the test project.

The seven-migration gap includes the account lifecycle, deletion request/manifest, worker, application purge, ownership, and restore-reconciliation migrations required by the local P0–P5 implementation. Applying them requires the repository's migration release process, backup/PITR confirmation, rollback plan, and human approval; this preflight does not substitute for those gates.

## Credential handling

The supplied management token was used only in ephemeral shell variables for read-only checks and was never persisted. Because the token was pasted into chat, it should be revoked/rotated after this preflight. A future worker credential must be separate from the Management API token and must not be exposed to browser or MCP clients.
