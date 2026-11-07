# R2 backup bucket — status and runbook

Date: 2026-11-07. Status: **BUCKET READY / DUMP BLOCKED** — the private bucket exists and is verified, but no S3 access key is configured, so no dump can be uploaded yet.

## Verified state

| Item | Value |
|---|---|
| Bucket name | `evidence-layer-db-backups` |
| Account ID | `7b52588f0e52079bcc49b92c6318056b` (present in the environment) |
| Location hint | `WEUR` |
| Creation date | `2026-11-07T21:03:49Z` |
| Token verification | `active` |
| Managed `r2.dev` domain | **disabled** |
| Custom domains | **0** |
| Public access | **none**: private bucket |
| S3 access keys (`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`) | **missing** |

The bucket already existed when the create call ran; the API returned `10004` ("bucket exists, and you own it"). Ownership, location and privacy were then verified with read-only calls.

## Privacy confirmation

- `GET /accounts/{id}/r2/buckets/evidence-layer-db-backups/domains/managed` → `enabled: false`.
- `GET /accounts/{id}/r2/buckets/evidence-layer-db-backups/domains/custom` → zero domains.

Do not enable the `r2.dev` subdomain or attach a custom domain to this bucket. Database dumps must stay private.

## S3 credentials

The Cloudflare API has no route for R2 S3 keys in this account (`/r2/api-tokens` and `/r2/buckets/{bucket}/keys` both return `404 / 10015`). Keys must be created in the dashboard:

**Cloudflare → R2 → Manage R2 API Tokens → Create API token**

- Permission: **Object Read & Write**
- Scope: **only** `evidence-layer-db-backups`
- Store the values in the secret manager as `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`.
- Never commit them or expose them through `NEXT_PUBLIC_*`.

Required variable names for later upload:

| Variable | Value |
|---|---|
| `R2_ACCESS_KEY_ID` | from the dashboard token |
| `R2_SECRET_ACCESS_KEY` | from the dashboard token |
| `R2_BUCKET` | `evidence-layer-db-backups` |
| `R2_ENDPOINT` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |

## Bucket create / verify (idempotent)

```sh
curl -sS -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets/evidence-layer-db-backups" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json"

curl -sS \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets/evidence-layer-db-backups" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

## Upload example (variables only, no secrets)

```sh
AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
aws s3 cp ./evidence-layer-YYYY-MM-DD.dump \
  "s3://$R2_BUCKET/dumps/YYYY-MM-DD/evidence-layer.dump" \
  --endpoint-url "$R2_ENDPOINT" \
  --only-show-errors
```

Suggested key layout: `dumps/<date>/<database>.dump`. Keep `.dump` files out of git.

## Retention

Recommended: **14 days**, with at least one restore-verified copy kept longer. No lifecycle rule was created; add one only after the data owner approves the retention policy.

## Bucket ready is not restore GO

The restore gate stays **NO-GO** until:

1. a real database dump is uploaded and its checksum recorded;
2. a non-production restore from that object completes;
3. row counts and a smoke query match the source;
4. operator, timestamp, and result are recorded.

Production Supabase still reports `pitr_enabled: false` and zero backups, so no recovery point exists today.

## Next actions

1. Create the bucket-scoped S3 key in the Cloudflare dashboard.
2. Store `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` in the secret manager.
3. With explicit approval, run `pg_dump`, upload, and record the checksum.
4. Perform the non-production restore drill before any migration apply.
