# R2 backup bucket — status and runbook

Date: 2026-11-07. Status: **BACKUP_VERIFIED** — the private bucket holds a real production dump with a recorded checksum, and that dump passed a non-production restore drill.

## Verified state

| Item | Value |
|---|---|
| Bucket | `evidence-layer-db-backups` |
| Account ID | `7b52588f0e52079bcc49b92c6318056b` |
| Location hint | `WEUR` |
| Creation date | `2026-11-07T21:03:49Z` |
| Cloudflare API token | `active` |
| Managed `r2.dev` domain | **disabled** |
| Custom domains | **0** |
| S3 endpoint | `https://7b52588f0e52079bcc49b92c6318056b.r2.cloudflarestorage.com` |
| Object count after probe | 0 (probe removed) |
| Backup object | `dumps/2026-11-07/evidence-layer.dump` |
| Backup size / SHA-256 | 127,007 bytes / `6450cabf…32b73e` |

## S3 credential probe

A tiny object was written, read, and deleted under `healthcheck/2026-11-07.txt`, then the bucket was listed to confirm cleanup.

| Operation | Result |
|---|---|
| `head_bucket` | OK |
| `put_object` | OK |
| `get_object` | OK |
| `delete_object` | OK |
| post-probe listing | 0 objects |

The credentials therefore have object read/write/delete inside this bucket. The probe object was removed.

## Environment names currently in use

| Variable | Purpose |
|---|---|
| `S3_API_ENDPOINT` | `https://7b52588f0e52079bcc49b92c6318056b.r2.cloudflarestorage.com` |
| `SECRET_ACCES_KEY_ID` | S3 access key id |
| `SECRET_ACCES_KEY` | S3 secret |

These names are non-standard (note the spelling). The runbook below uses the normalized names. Renaming the stored secrets is optional but recommended for consistency:

| Normalized | Maps to |
|---|---|
| `R2_ENDPOINT` | `S3_API_ENDPOINT` |
| `R2_ACCESS_KEY_ID` | `SECRET_ACCES_KEY_ID` |
| `R2_SECRET_ACCESS_KEY` | `SECRET_ACCES_KEY` |
| `R2_BUCKET` | `evidence-layer-db-backups` |

Never commit these values or expose them through `NEXT_PUBLIC_*`.

## Privacy

- `GET .../domains/managed` → `enabled: false`
- `GET .../domains/custom` → zero domains

Keep both disabled. Database dumps must stay private.

## Upload example (variables only)

```sh
AWS_ACCESS_KEY_ID="$SECRET_ACCES_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$SECRET_ACCES_KEY" \
aws s3 cp ./evidence-layer-YYYY-MM-DD.dump \
  "s3://evidence-layer-db-backups/dumps/YYYY-MM-DD/evidence-layer.dump" \
  --endpoint-url "$S3_API_ENDPOINT" \
  --only-show-errors
```

Suggested layout: `dumps/<date>/<database>.dump`. Keep `.dump` files out of git and record a SHA-256 next to the object key.

## Retention

Recommended: **14 days**, with at least one restore-verified copy kept longer. No lifecycle rule was created; add one only after the data owner approves the retention policy.

## Restore evidence

A production dump of `public` + `private` was uploaded and restored into a disposable PostgreSQL 17 instance: **0 restore errors**, matching row counts (2 workspaces, 2 memberships, 0 evidence), 32 policies, and 8 validated FKs. Details: `docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-07.md`.

Limits: `auth`, `storage`, and `extensions` are not in the dump, and PITR is still disabled.

## Bucket ready is not restore GO

The restore gate remains **NO-GO** until:

1. a real database dump is uploaded and its checksum recorded;
2. a non-production restore from that object completes;
3. row counts and a smoke query match the source;
4. operator, timestamp, and result are recorded.

Production Supabase still reports `pitr_enabled: false` and zero backups. This document only proves storage readiness.

## Next actions

1. With explicit approval and `DATABASE_URL`, run `pg_dump` and compute its SHA-256.
2. Upload it under `dumps/<date>/` and store the checksum in the operations log.
3. Restore it into a disposable database and run the smoke query.
4. Only then can the restore gate be re-evaluated.

No migration, flag change, worker, dump, or restore was performed in this step.
