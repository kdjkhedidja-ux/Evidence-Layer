# Production restore verification — 2026-11-07

Read-only attempt. **No restore was executed.** This document records what could be verified and the minimum evidence an operator must capture before any migration apply.

## Attempt result

| Action | Result | Detail |
|---|---|---|
| `GET /v1/projects/slxfsjnsrvfkdtxayiud` with the available management token | **403** | The token does not have access to this project. |
| `GET /v1/projects/slxfsjnsrvfkdtxayiud/database/backups` | **403** | `Missing required permission(s): backups_read`. |
| `pitr_enabled`, latest backup, retention | **UNKNOWN** | Not readable with current access. |

A management token exists in the environment, but it is scoped to another project/organisation. It was not used to modify anything.

## Why this blocks a migration apply

The deletion migrations are additive, but a failed or partial apply still requires a verified recovery point. Without `pitr_enabled` and a recent backup timestamp, the release cannot claim rollback safety.

## Minimum evidence to capture before any apply

Record all of the following from the Supabase dashboard (`Database → Backups`) or the Management API with a token that has `backups_read`:

| Field | Example / source | Required |
|---|---|---|
| Project ref | `slxfsjnsrvfkdtxayiud` | yes |
| Project name and organisation | Dashboard project settings | yes |
| Plan and PITR add-on | Dashboard billing / backups page | yes |
| `pitr_enabled` | `GET /v1/projects/{ref}/database/backups` → `pitr_enabled` | yes |
| Latest backup timestamp (UTC) | `backups[]` / `physical_backup_data` | yes |
| Backup retention window | Dashboard backups page | yes |
| Backup region / storage location | Dashboard project settings | yes |
| Restore owner (named person) | Runbook/roster | yes |
| Restore destination for a drill | Disposable project or isolated DB | yes |
| Date of last successful restore drill | Operations log | yes |
| Chosen RPO / RTO and acceptance | Owner decision | yes |

## Safe read-only commands

```sh
REF=slxfsjnsrvfkdtxayiud
curl -s -H "Authorization: Bearer $TOKEN_WITH_BACKUPS_READ" \
  "https://api.supabase.com/v1/projects/$REF"
curl -s -H "Authorization: Bearer $TOKEN_WITH_BACKUPS_READ" \
  "https://api.supabase.com/v1/projects/$REF/database/backups"
```

Expected minimum in the second response: `pitr_enabled: true` **or** at least one recent entry in `backups` / `physical_backup_data`, plus a recorded timestamp within the agreed window.

## Do not do in this step

- Do not trigger a restore.
- Do not create a new project for restore without owner approval.
- Do not paste tokens or backup URLs into git, docs, or chat.

## Decision

**NO-GO for the deletion migration apply.** The smoke's authenticated checks are unrun and the production recovery point is unverified. The core public/auth surface passed the anonymous checks, but that does not establish rollback safety for a schema change.

Re-evaluate when:

1. `production_smoke.py` has run with a dedicated test account and the result is recorded, and
2. a backup/PITR recovery point for `slxfsjnsrvfkdtxayiud` is captured above with a named restore owner.
