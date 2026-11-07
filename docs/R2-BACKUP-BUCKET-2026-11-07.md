# R2 backup bucket — status and runbook

Date: 2026-11-07. Status: **BLOCKED**. No bucket was created because no Cloudflare credential is available in this environment.

## Preconditions verified

| Check | Result |
|---|---|
| Cloudflare account ID in env | **missing** |
| `CLOUDFLARE_API_TOKEN` / `CF_API_TOKEN` | **missing** |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | **missing** |
| Wrangler OAuth session | **not authenticated** (`wrangler whoami`: "You are not authenticated") |
| `.dev.vars` / tracked env files with Cloudflare names | none |

No secret value was read or printed. The environment has no names matching `CLOUDFLARE*`, `CF_*`, `R2_*`, or `AWS_*`.

## Required configuration (names only)

| Variable | Purpose |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Account that owns the bucket. |
| `CLOUDFLARE_API_TOKEN` | Token with **Workers R2 Storage: Edit** for bucket creation. |
| `R2_ACCESS_KEY_ID` | S3-compatible key for uploads. |
| `R2_SECRET_ACCESS_KEY` | S3-compatible secret for uploads. |
| `R2_BUCKET` | `evidence-layer-db-backups` |
| `R2_ENDPOINT` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |

A DNS-only token is **not** sufficient. Without R2 bucket permissions and S3 keys, stop here.

## Create the bucket (operator, once credentials exist)

Buckets are private by default; do not enable `r2.dev` public access or a custom domain.

```sh
curl -sS -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets/evidence-layer-db-backups" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json"
```

Verify it exists and has no public endpoint:

```sh
curl -sS \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets/evidence-layer-db-backups" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

Treat "no `r2.dev` subdomain and no custom domain" as the privacy confirmation. Never enable public access for database dumps.

## S3 access keys

Dashboard path: **Cloudflare → R2 → Manage R2 API Tokens → Create API token**.

- Permission: **Object Read & Write**
- Scope: **only** `evidence-layer-db-backups`
- Save `Access Key ID` and `Secret Access Key` in the secret manager as `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`.
- Do not commit them or place them in `NEXT_PUBLIC_*`.

## Upload example (no real secrets)

```sh
AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
aws s3 cp ./evidence-layer-2026-11-07.dump \
  "s3://$R2_BUCKET/dumps/2026-11-07/evidence-layer.dump" \
  --endpoint-url "$R2_ENDPOINT" \
  --only-show-errors
```

Store object keys as `dumps/<date>/<database>.dump`. Keep `.dump` files out of git.

## Retention

Recommended: **14 days** for dump objects, with at least one restore-verified copy retained longer. Do not enforce a lifecycle rule in this step; add one only after confirming the API and retention policy with the data owner.

## Bucket ready is not restore GO

Creating the bucket proves only that storage exists. The restore gate remains **NO-GO** until:

1. a real database dump is uploaded with a recorded checksum;
2. a non-production restore from that dump completes;
3. row counts and a smoke query are compared against the source;
4. the operator, timestamp, and result are recorded.

Current production blocker: `pitr_enabled: false` and zero backups, so no recovery point exists today.

## Next actions

1. Provide `CLOUDFLARE_ACCOUNT_ID` and an R2-capable `CLOUDFLARE_API_TOKEN` in the environment.
2. Re-run the create and verify commands above.
3. Create bucket-scoped S3 keys and store them as `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`.
4. Then, and only with explicit approval, perform a `pg_dump` and upload.
