# RLS and Storage Integration Evidence Report

Date: 2026-10-30. Scope: local development and isolated test environments only;
no hosted project was used.

## Status

**Updated 2026-10-30 (final).** Gate 4 is now evidenced: the live Auth/Storage
integration tests and a real end-to-end HTTP read through the Evidence Layer
service both passed against an isolated local Supabase stack. See
"Gate 4 live evidence (2026-10-30)" below. The earlier sections of this report
are retained as the historical record of how the gate was reached.

## Migration status — existing local project

All twelve versions below were observed in
`supabase_migrations.schema_migrations` for the existing local
`evidence-layer-local` database. Versions 1–7 were applied through the local
Supabase migration workflow; versions 8–12 were subsequently applied directly
to that isolated local database with `psql`.

| Version | Migration | Status |
|---|---|---|
| 20261007170000 | platform_baseline | applied |
| 20261008000000 | rls_auto_enable_prerequisite | applied |
| 20261016100000 | saas_identity_workspace_boundary | applied |
| 20261018150000 | workspace_scoped_evidence_records | applied |
| 20261019135502 | personal_workspace_bootstrap | applied |
| 20261019152707 | restrict_rls_auto_enable_execution | applied |
| 20261022100000 | source_snapshot_catalog | applied |
| 20261022110000 | source_snapshot_storage_policies | applied |
| 20261022120000 | snapshot_verifier_identity | applied |
| 20261022130000 | snapshot_verifier_fail_closed_guards | applied |
| 20261023150000 | snapshot_verifier_admin_audit_guards | applied |
| 20261028120000 | rls_auto_enable_fail_closed | applied |

This status describes the existing local database only. It does not mean the
migrations were applied to the separate temporary test project attempted below.

## Direct SQL assertions completed

### Tenant RLS

Direct SQL checks using `SET ROLE` and `request.jwt.claim.sub` passed with the
expected results:

| # | Assertion | Expected | Actual |
|---|---|---|---|
| T1 | RLS enabled and forced on workspaces, memberships, evidence records, snapshots, and grants | all `t/t` | all `t/t` |
| T2 | Member with a `read` grant sees the published snapshot | 1 | 1 |
| T3 | Member without a grant cannot see the published snapshot | 0 | 0 |
| T4 | Non-member cannot see workspace snapshots | 0 | 0 |
| T5a | Owner sees their staged snapshot | 1 | 1 |
| T5b | Non-owner member cannot see a staged snapshot | 0 | 0 |
| T6 | `anon` cannot see snapshots | denied | permission denied |
| T7a | Grantee sees their `source_grants` row | 1 | 1 |
| T7b | Non-grantee cannot see that grant | 0 | 0 |
| T8 | Member sees only their own workspace | 1 | 1 |

The setup also observed the snapshot immutability triggers and foreign-key
checks against `auth.users`.

### Snapshot verifier identity and RPC guards

The following direct SQL checks passed on the existing local database:

| # | Assertion | Result |
|---|---|---|
| V1 | Verifier and publisher-owner roles are isolated NOLOGIN/NOINHERIT roles without elevated attributes | passed |
| V2 | Verifier allowlist is empty by default | 0 rows |
| V3 | Allowlist RLS is enabled and forced | `t/t` |
| V4 | Non-allowlisted caller cannot begin verification | denied |
| V5 | Non-allowlisted caller cannot publish a verified snapshot | denied |

These are database-level checks; they do not prove Auth or Storage HTTP behavior.

## Database (pgTAP) test result — Gate 3, passed 366/366

The full pgTAP suite was executed against an isolated PostgreSQL container and
all assertions passed. Source: [`VERIFICATION-GATE-REPORT.md`](../VERIFICATION-GATE-REPORT.md),
dated 2026-10-29, Gate 3.

- Environment: container `evidence-gate-isolated-pg`, image
  `supabase/postgres:17.11.0.004` (PostgreSQL 17.11.0.004).
- pgTAP version: 1.3.3.
- Result: **366 assertions passed, 0 failed** across the 7 files under
  `supabase/tests/`, including `source_snapshot_storage.test.sql` (**68/68**).
- The suite used a minimal Storage schema (`storage.buckets`, `storage.objects`)
  plus standard Storage grants, and simulated JWT claims via `set_config`,
  because the bare image omits the Storage and Auth services.

The isolated container is currently stopped. Its success is a documented
historical result, not evidence that the live Storage or Auth services are
running now.

### The two database environments

| Environment | Result |
|---|---|
| `evidence-gate-isolated-pg` (isolated PostgreSQL 17.11.0.004) | Gate 2 (12 migrations) and Gate 3 (pgTAP 366/366) passed per `VERIFICATION-GATE-REPORT.md` |
| `evidence-layer-local` (Docker Supabase local DB) | Migrations and direct SQL checks passed; a later pgTAP re-run stopped at fixture setup |

## Storage pgTAP — local re-run attempt on the Docker database

The full pgTAP suite already passed 366/366 on the isolated container (Gate 3
result above). The following describes a *later* re-run attempt against the
existing `evidence-layer-local` Docker database, which stopped during fixture
setup:

`supabase/tests/source_snapshot_storage.test.sql` declares `plan(68)`.

- A direct `psql` run against the existing local database passed assertions
  1–21, covering policy inventory/signatures, bucket settings, and bucket
  helper behavior.
- Fixture setup then failed inserting workspace
  `a1000000-0000-4000-8000-000000000001`, because that fixed ID already exists
  in the existing database.
- Assertions after the fixture setup, including the Storage role-behavior
  assertions, did not run in this re-run; the test did not complete its
  68-assertion plan in this re-run.
- This is a fixture collision on a dirty database, not a Storage-policy test
  failure, and it does **not** negate the Gate 3 result (366/366 on the
  isolated container). The aborted transaction was rolled back when the `psql`
  session ended; no existing rows were deleted.

A separate `supabase test db --local` attempt failed before executing test
assertions: the `pg_prove` container timed out connecting to `db` at
`172.18.0.2:5432`. This is a runner-to-database connectivity failure, not a
test result.

## Separate temporary project attempt

To avoid the fixed-ID collision, a temporary project copy was prepared under
`/tmp` with a distinct project ID and API/database ports (`55321`/`55322`).
The migration and test files were copied unchanged.

The temporary `supabase start` failed during database schema initialization.
CLI debug output reported a Node PostgreSQL client `timeout expired`; the CLI
then removed that temporary project's database volume and Docker network. No
migrations or pgTAP assertions ran in that project. The existing
`evidence-layer-local` database was not reset or restarted for this attempt.

The timeout's root cause remains undetermined. The CLI also reported a missing
local Supabase profile file, but available evidence does not establish that it
caused the database timeout.

## Python test suite

The repository Python suite completed with **962 passed, 58 subtests passed,
and 10 skipped**. These unit and local tests are a separate evidence tier and
do not replace live Auth/Storage integration tests.

## Gate 4 live evidence (2026-10-30)

All Gate 4 items were exercised against a disposable, fully isolated local
Supabase project (`evidence-layer-integration-*`, short unique id, dedicated
port range, own containers/volumes/network). The existing
`evidence-layer-local` stack was never modified, and the run cleaned up after
itself (no leftover containers, volumes, networks, or firewall rules; the
original database remained `Up (healthy)` throughout).

### Root causes of the earlier startup failure

The `DbSetupError: error running container: exit 1` seen in the earlier
attempt above was diagnosed and resolved without touching migrations, RLS, or
the existing stack:

1. The Docker daemon in this environment programs `iptables-legacy` while the
   default `FORWARD` policy is `DROP`, and no per-bridge accept rule is added
   for newly created networks. Containers on the new isolated bridge therefore
   could not reach each other (proven: `pg_isready` from a container on the
   same network timed out; adding a rule scoped to that one bridge
   `-i br-X -o br-X -j ACCEPT` fixed it immediately). The rule was removed with
   the network after the run.
2. The `postgres` role in the `supabase/postgres:17.11` image is not a
   superuser; operator seeding of test fixtures (for example the snapshot
   verifier allowlist, whose RLS is forced) must connect as `supabase_admin`.
3. Supabase CLI v2.121 redacts the legacy JWT `ANON_KEY` in `status` output;
   the `PUBLISHABLE_KEY` (`sb_publishable_…`) is the working local apikey.
4. PostgREST maps PL/pgSQL `raise exception … errcode 'P0002'` to HTTP 500.
   Tests accept a 500 **only** when the response body's `code` is exactly the
   expected PostgreSQL error code (`is_rejected(response, postgrest_code=…)`);
   any other 500 fails the test.
5. Long project ids are truncated in Docker DNS names; the runner now uses a
   short unique id and asserts the length before starting.

### Live service-level test results

Command (environment provided by `tests/integration/run_local_supabase.py`):

```
python -m pytest -p no:cacheprovider tests/integration
```

**18 passed, 0 failed, 0 skipped** in ~164 s, comprising:

- `test_local_supabase_storage_source_read.py` + `test_local_storage_snapshot_policies.py`
  (**10 tests**): real GoTrue signup with email confirmation via Mailpit, real
  JWT verified via JWKS and Auth `/user`, real Storage upload/read, grant
  allow/deny, workspace-scoped verifier allowlist, SHA-256 recomputation by
  the reader, revocation blocking subsequent reads, and forged-token rejection.
- `test_local_http_source_content.py` (**8 tests**): the real FastAPI
  application (`create_app`, no test overrides of verifier/directory/read-path)
  served by uvicorn on loopback, composed against the isolated stack:

  | Case | Result |
  |---|---|
  | Authorized member reads published snapshot via `GET /v1/workspaces/{id}/sources/{id}/versions/{id}/content` | 200, exact bytes, `Content-Digest: sha-256` matches |
  | Missing / invalid bearer token | 401 |
  | User without workspace membership | 404, no content leaked |
  | Member without `source_grants` read grant | 403/404, no content leaked |
  | Staged (unpublished) snapshot | denied, bytes never delivered |
  | Read allowed, grant revoked, next read | 200 then denied after revocation |
  | Staged digest ≠ uploaded bytes | publication rejected (`P0002`); service never delivers |
  | Alternative path variants (`/raw`, `/download`, …) without auth | 401/404/405, no content leaked |

Supporting suites: **962 passed, 58 subtests passed** (local Python suite),
**123 passed** (snapshot verifier + health), **193 passed** (six service-layer
read-path test modules).

### Cleanup verification after the live runs

```
containers: none   volumes: none   networks: none   bridge rule: removed
original: supabase_db_evidence-layer-local Up (healthy)
```

### Scope notes

- Read authorization is independent of any provider-transfer (egress/JEV)
  grant by construction; egress is not activated and `/retrieve` remains
  closed. A positive egress-authorization test becomes possible only when an
  egress feature is designed.
- The CI workflow was intentionally **not** modified. Before enabling these
  live tests in CI, the runner must provision the scoped bridge firewall rule
  (or the CI Docker daemon must not impose a dropping FORWARD policy on new
  bridges); this belongs to the CI-enablement slice, not to the gate itself.

## Gate decision

- **Gate 2 (migrations): passed.** All 12 migrations applied and runtime-verified.
- **Gate 3 (database pgTAP): passed, 366/366.** Proven on the isolated
  `evidence-gate-isolated-pg` container per `VERIFICATION-GATE-REPORT.md`.
- **Gate 4 (live Auth/Storage integration): passed (2026-10-30).** Real Auth
  (GoTrue/HTTP, JWT via JWKS + `/user`), real Storage API reads/writes, live
  snapshot reads with full-path SHA-256 verification, pre-delivery
  re-authorization, revocation enforcement, and fail-closed denials were all
  exercised against running services — including a real end-to-end HTTP read
  through the Evidence Layer service itself.

`/retrieve` and external egress remain closed by separate, independent
decisions; passing Gate 4 does not open them.
