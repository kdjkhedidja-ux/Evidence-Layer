# Production restore verification — 2026-11-07

Read-only attempt. **No restore was executed.** This document records what could be verified and the minimum evidence an operator must capture before any migration apply.

## Attempt result

| Action | Result | Detail |
|---|---|---|
| `GET /v1/projects/slxfsjnsrvfkdtxayiud` | **200** | `Evidence Layer`, `ACTIVE_HEALTHY`, `eu-central-1`, created `2026-10-07T19:21:06Z`, org `vvveiefymwolihvgpflb`. |
| `GET /v1/projects/slxfsjnsrvfkdtxayiud/database/backups` | **200** | `pitr_enabled: false`, `walg_enabled: true`, `backups: 0`, `physical_backup_data: 0`, region `eu-central-1`. |
| Recovery point available | **FAIL** | No backup entry and PITR disabled: there is no recoverable point recorded by the API. |

The earlier `403` was caused by a token scoped to another project. With the production-scoped token the metadata is readable, and it shows **no usable recovery point**.

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

**NO-GO for the deletion migration apply.** The production project reports `pitr_enabled: false` and zero backups, so there is no verified recovery point. Authenticated smoke checks are also unrun. The core public/auth surface passes anonymous checks, but that does not establish rollback safety for a schema change.

Re-evaluate when:

1. `production_smoke.py` has run with a dedicated test account and the result is recorded, and
2. a backup/PITR recovery point for `slxfsjnsrvfkdtxayiud` is captured above with a named restore owner.
