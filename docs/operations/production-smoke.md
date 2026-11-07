# Production smoke test (read-only)

Purpose: detect whether the deployed Evidence Service still serves protected routes. This is the check that answers the open question from the 2026-11-07 verification: does the account-lifecycle gate return 503 because `account_lifecycle` is missing?

## What it does

`scripts/production_smoke.py` performs read-only GET requests only:

1. `GET /healthz` without a token.
2. `GET /v1/me` with a test user token.
3. `GET /v1/workspaces/{id}/evidence` for the first workspace, or the one passed with `--workspace-id`.

It never writes, never deletes, never calls a model, and never prints the token.

## Run

```sh
EVIDENCE_SERVICE_URL=https://<evidence-service> \
SMOKE_ACCESS_TOKEN=<test-user-jwt> \
python scripts/production_smoke.py --json
```

Optional: `--workspace-id <uuid>` to pin a known workspace.

## Reading the result

| Result | Meaning |
|---|---|
| All HTTP 200 | Service and lifecycle gate healthy for that account. |
| `/v1/me` 403 | The test account is not `available` (blocked/revoked). Use another account. |
| `/v1/me` 503 | Service unavailable, or the lifecycle read fails. If the database lacks `account_lifecycle`, this is the expected symptom of the schema gap. |
| `/healthz` non-200 | Service is down; stop before interpreting data results. |
| 401 | Token expired or invalid. |

## Limits

- Uses a real test account; do not run with a customer token.
- Read-only: it does not verify deletion request/accept behaviour. That requires the write smoke described in `docs/operations/production-migration-plan-2026-11-07.md`.
- A 200 does not prove the deletion tables exist; it only proves the current code path works.
