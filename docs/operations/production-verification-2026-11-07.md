# Production verification — 2026-11-07

Scope: read-only checks against the live deployment `https://evidence-layer.vercel.app` and its public Supabase project. No production migration was applied and no authenticated session was used.

## Method

- Static audit of the repository for elevated keys and public environment variables.
- Client bundle inspection to recover the public Supabase URL and publishable key (public by design).
- Anonymous PostgREST probes with the publishable key only, to test whether tables are exposed without a session.
- No service-role key, no management token, and no user credentials were used.

## Verified — PASS

| Check | Result | Evidence |
|---|---|---|
| No `service_role` on user request paths | PASS | `evidence_repository.py:75`, `supabase_source_catalog.py:6`, `secure_snapshot_read.py:803`; the deployment test forbids the key in `.env.example` (`apps/web/tests/config-and-secrets.test.ts:48`). |
| Only public Supabase variables reach the browser | PASS | Client bundle exposes only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`; no secret variable is referenced in `apps/web/lib` or `apps/web/app`. |
| No environment or secret files tracked | PASS | `git ls-files` shows only `.env.example` files and `docs/ENVIRONMENT_AND_SECRETS.md`. |
| Anonymous SELECT is denied on core tables | PASS | `evidence_records`, `evidence_relationships`, `workspaces`, `workspace_memberships`, `source_grants`, `source_snapshots` return HTTP 401 / SQLSTATE `42501`: the role lacks SELECT, so access fails closed rather than returning rows. |
| Production project reachable | PASS | `https://slxfsjnsrvfkdtxayiud.supabase.co` responds; the publishable key authenticates the project, not a database role. |

## Critical finding — production schema is behind the code

The live database does **not** contain the account-lifecycle and deletion tables. Anonymous PostgREST probes return SQLSTATE `PGRST205` ("Could not find the table"):

- `public.account_lifecycle`
- `public.data_deletion_requests`
- `public.data_deletion_manifest`
- `public.data_deletion_work_set`
- `public.evidence_deletion_audit`

Consequences:

- The **Delete account** panel shipped in PR #63 cannot complete a request: the BFF forwards to the Evidence Service, which queries tables that do not exist, and the UI reports an unavailable service.
- If the deployed Evidence Service contains the account-lifecycle gate, every protected `/v1` request would also fail closed because the lifecycle read fails. This could not be tested without an authenticated session; treat it as a release risk, not a confirmed outage.
- The local migration guard is green for the reviewed schema, but local review does not apply migrations to the live project.

## PENDING — requires operator access

| Check | Why it is pending | Required action |
|---|---|---|
| Backup / PITR enabled and recent for `slxfsjnsrvfkdtxayiud` | Requires a management credential with project access | Verify a recovery point before any migration apply |
| Authenticated RLS behaviour (owner/member isolation) | Requires a test user session | Run the repository's RLS tests against the live project, or a restored copy |
| Absence of `service_role` in Vercel environment variables | Requires Vercel project access | Confirm only publishable/URL variables and server-side service URL are present |
| Secret rotation and least-privilege review | Requires operator policy | Rotate any credential shared outside the secret manager; verify service-account scopes |
| Alerting on 5xx / deletion failures | Requires observability access | Add error-rate and deletion-request failure alerts |
| Production restore drill | Requires a disposable restore target | Run the documented drill before claiming restore consistency |

## Recommended actions, in order

1. **Do not enable or advertise account deletion until the production schema is migrated.** The UI currently fails closed, which is safe but non-functional.
2. Verify a recent backup/PITR recovery point for the live project.
3. Apply the reviewed migrations through the repository release process (manifest + statement ledger + CI), then re-run the probes below.
4. Confirm the Evidence Service account gate and deletion endpoints with an authenticated staging user.
5. Decide whether to keep the Delete account panel hidden behind a feature flag until step 3 is complete.

## Re-run commands

```sh
# Schema presence (anonymous; public key only)
URL=https://slxfsjnsrvfkdtxayiud.supabase.co
KEY=<publishable key>
for t in account_lifecycle data_deletion_requests evidence_deletion_audit; do
  curl -s -o /dev/null -w "$t %{http_code}\n" -H "apikey: $KEY" "$URL/rest/v1/$t?select=*&limit=1"
done
```

Expected after migration: HTTP 200 with an empty array (RLS) or 401/403, never `PGRST205`.

## Status

- Local static security review: **PASS**
- Production schema alignment: **FAIL (schema behind code)**
- Account deletion in production: **BLOCKED** until migrations are applied and verified
- Backups/PITR and authenticated RLS: **PENDING operator verification**
