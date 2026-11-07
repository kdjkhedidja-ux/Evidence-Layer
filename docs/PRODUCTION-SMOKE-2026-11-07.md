# Production smoke — 2026-11-07

Read-only. No migration, no restore, no worker, no feature-flag change, no write to production data.

## Configuration used

| Variable | Status | Notes |
|---|---|---|
| `EVIDENCE_SERVICE_URL` | available | Configured in the Codespace environment; the value is not recorded here. |
| `SMOKE_ACCESS_TOKEN` | missing | No dedicated test-user session; the password-grant helper can obtain one (see below). |
| Supabase publishable key | available (public) | Recovered from the public client bundle; safe by design. |

Authenticated checks in `scripts/production_smoke.py` therefore **could not run**. The anonymous checks below were executed directly.

## Results

| # | Check | Result | Code | Note |
|---|---|---|---|---|
| 1 | `/` public site | PASS | 200 | |
| 2 | `/documentation` | PASS | 200 | |
| 3 | `/cost-performance` | PASS | 200 | |
| 4 | `/privacy` | PASS | 200 | |
| 5 | `/terms` | PASS | 200 | |
| 6 | `/login` | PASS | 200 | |
| 7 | `/signup` | PASS | 200 | |
| 8 | `/forgot-password` | PASS | 200 | |
| 9 | Supabase Auth health | PASS | 200 | `auth/v1/health` |
| 10 | BFF `/api/evidence/me` without session | PASS | 401 | Fails closed before any service call. |
| 11 | BFF workspace route without session | PASS | 401 | Fails closed. |
| 12 | Anonymous SELECT `workspaces` | PASS | 401 / 42501 | Table exists; no privilege for anon. |
| 13 | Anonymous SELECT `workspace_memberships` | PASS | 401 / 42501 | |
| 14 | Anonymous SELECT `evidence_records` | PASS | 401 / 42501 | |
| 15 | Anonymous SELECT `evidence_relationships` | PASS | 401 / 42501 | |
| 16 | Anonymous SELECT `source_snapshots` | PASS | 401 / 42501 | |
| 17 | Anonymous SELECT `source_grants` | PASS | 401 / 42501 | |
| 18 | `account_lifecycle` present | **FAIL (expected)** | 404 `PGRST205` | Deletion/lifecycle schema not applied. |
| 19 | `data_deletion_requests` present | **FAIL (expected)** | 404 `PGRST205` | |
| 20 | `evidence_deletion_audit` present | **FAIL (expected)** | 404 `PGRST205` | |
| 21 | Service `/healthz` | PASS | 200 | `{"status":"ok"}` on the deployed service. |
| 22 | Service `/v1/me` without token | PASS | 401 | Route present; requires a bearer token. |
| 23 | Service `/v1/workspaces/{id}` without token | PASS | 401 | Core route present. |
| 24 | Service `/v1/workspaces/{id}/sources` without token | PASS | 401 | Core route present. |
| 25 | Service `/v1/workspaces/{id}/evidence` without token | PASS | 401 | Core route present. |
| 26 | Service `/v1/workspaces/{id}/relationships` without token | PASS | 401 | Core route present. |
| 27 | Service source content route without token | PASS | 401 | Core route present. |
| 28 | Service `/v1/deletion-requests` | **FAIL (expected)** | 404 | Deployed service predates the deletion endpoints. |
| 29 | Service `/v1/deletion-requests/{id}` and `/accept` | **FAIL (expected)** | 404 | Same. |
| 30 | Service `/v1/me` with test user | SKIP | — | No dedicated test-user credentials. |
| 31 | Workspace evidence list with test user | SKIP | — | Same. |

## Interpretation

- **Core live surface shows no unexpected breakage** in the checks available without a session: public pages, Auth, BFF fail-closed behavior, and RLS denial on every core table.
- **Deletion/lifecycle tables are absent**, as expected from the audit. Any route that reaches PostgREST for those tables will fail; the Delete account UI remains feature-flagged off.
- **The deployed Evidence Service predates the deletion work.** All core `/v1` routes exist (401 without a token), while every deletion route returns 404. The account-lifecycle gate is therefore **not active in production**, which removes the earlier 503 risk on core routes.
- **Authenticated behavior is still unverified**: no dedicated test user was available, so owner/member authorization and real CRUD responses remain unproven by this smoke.
- **Deletion requires both** a service deployment that contains the new routes and the database migrations. Neither is present.

## Obtaining a test token (password grant)

`access_token` is not available from Edge Function Secrets. Use a dedicated test user and the publishable key:

```sh
curl -sS -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $SUPABASE_PUBLISHABLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"<test-user-email>","password":"<test-user-password>"}'
```

The response contains a short-lived `access_token`. Do not store it, commit it, or paste it into chat.

The helper below performs the grant in memory, never prints the token, and immediately runs the read-only smoke:

```sh
EVIDENCE_SERVICE_URL=https://<service> \
SUPABASE_URL=https://<project>.supabase.co \
SUPABASE_PUBLISHABLE_KEY=<publishable-key> \
SMOKE_USER_EMAIL=<test-user> \
SMOKE_USER_PASSWORD=<test-password> \
python scripts/production_smoke_auth.py --json
```

Use a test account only. The token is removed from the process environment before exit.

## Operator checklist (authenticated smoke)

Prerequisites:

- `EVIDENCE_SERVICE_URL` from the Vercel project (server-only variable; do not paste into chat).
- `SMOKE_ACCESS_TOKEN`: access token for a dedicated test account in `available` state. Do not use a customer account.
- Optional `--workspace-id` for a known workspace owned by the test account.

Command:

```sh
EVIDENCE_SERVICE_URL=https://<service> \
SMOKE_ACCESS_TOKEN=<test-jwt> \
python scripts/production_smoke.py --json
```

Success:

- `/healthz` 200 `{"status":"ok"}`.
- `/v1/me` 200 with `user_id` present and at least one workspace.
- Workspace evidence list 200 with an array.

Expected failure to record, not to fix here:

- `/v1/me` 503 with a valid token → the lifecycle gate cannot read `account_lifecycle`. This confirms the schema gap affects production reads.
- `/v1/me` 403 → the chosen account is not `available`; use another test account.

## Safety

- No rows were created, updated, or deleted.
- No token or key value is recorded in this document.
- The Delete account flag was not enabled.
