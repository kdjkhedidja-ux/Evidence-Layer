# Evidence-Layer Verification Gate Report

- Date: 2026-10-29
- Branch: `fix/rls-auto-enable-fail-closed`
- HEAD: `374d62f94d3f0e1dd2fbdf5e78469596a8c95224` (restore drill report commit)
- Guard commit: `92c8975090cc87d2216215174e06ae2b84c28eb8` ("Harden migration guard role and policy checks")

## Gate 1 — Migration guard (code) — PASSED

- Command: `python3 -m unittest tests.test_supabase_migration_guard`
- Result: `Ran 71 tests in 0.291s — OK`
- The tested code is exactly the guard commit `92c8975…c28eb8`, present on
  `origin/fix/rls-auto-enable-fail-closed` (it is the parent of HEAD).
- The working tree was clean at test time (no uncommitted changes), so the
  bytes tested match the reviewed commit.
- Scope: unit tests only — guard planning, local migration inventory,
  approval ledger, and Supabase Management-API parsing.
- Not claimed here: integration tests, pgTAP database tests, or live Supabase
  behavior. Those are tracked by Gates 2–4 below.

## Gate 2 — Security migrations — CLOSED (runtime-verified)

- 12 migrations are listed in `supabase/migrations.manifest.json` (with sha256
  integrity hashes).
- Blocker fixed: `CREATE ROLE snapshot_verifier` and `CREATE ROLE
  snapshot_publisher_owner` were written inside PL/pgSQL `DO` blocks as direct
  statements; both are now wrapped in `EXECUTE` in `20261022100000_source_snapshot_catalog.sql`
  and `20261022120000_snapshot_verifier_identity.sql`.
- Runtime verification (isolated PostgreSQL 17.11, image
  `supabase/postgres:17.11.0.004`, container `evidence-gate-isolated-pg`): all 12
  migrations applied in order with `ON_ERROR_STOP=1` and every one exited 0. A
  minimal Storage stub (`storage.buckets`, `storage.objects`) was provisioned
  because the bare image omits the Storage schema.
- Resulting state: `snapshot_verifier`, `snapshot_publisher_owner`, and
  `snapshot_verifier_allowlist_owner` are NOLOGIN/NOINHERIT/NOBYPASSRLS/NOSUPERUSER;
  `private.snapshot_verifier_allowlist` is empty (0 rows), so publication stays
  DENY_ALL by default.
- Guard suite (71) and static content suites (135) still pass.

## Gate 3 — Database (pgTAP) tests — CLOSED (all assertions pass)

- 7 pgTAP files under `supabase/tests/` run against the isolated PostgreSQL 17.11
  (container `evidence-gate-isolated-pg`, `pgtap` 1.3.3):
  - `evidence_records.test.sql`
  - `personal_workspace.test.sql`
  - `rls_auto_enable.test.sql`
  - `snapshot_verifier_identity.test.sql`
  - `source_snapshot_storage.test.sql`
  - `source_snapshots.test.sql`
  - `workspace_boundary.test.sql`
- Result: 366 assertions passed, 0 failed.
- Note: a minimal Storage schema (`storage.buckets`, `storage.objects`) plus the
  standard Storage grants were provisioned because the bare image omits the
  Storage service; the RLS/Storage policy assertions then passed against it.

## Gate 4 — Final review and approval — CLOSED

- Reference point adopted: `e8d5c122cc794d555076ce754f5995dfe7e8c5a8`
  (branch `fix/rls-auto-enable-fail-closed`, synced with `origin`).

### Verified (passed)
- Migration guard unit tests: 71 passed.
- All 12 migrations apply in order on isolated PostgreSQL 17.11; verifier roles are
  NOLOGIN/NOINHERIT/NOBYPASSRLS/NOSUPERUSER and the allowlist is empty (DENY_ALL).
- pgTAP: 366 assertions passed, 0 failed (tenant isolation, RLS/Storage policies,
  source_snapshots catalog, source_grants, verifier identity, negative/deny cases).
- Static content suites: 135 passed.

### Not tested / documented exceptions
- Live Supabase Storage service: verifier RPCs and the "storage.objects.version
  changes on every upload" assumption were exercised against a minimal Storage
  schema, not a running Storage service.
- Live Supabase Auth: JWT claims were simulated via set_config, not a running
  GoTrue service.
- Hosted Supabase project (PROJECT_REF ayrtyjgqlgtuiwuyigbz): no plan/verify/apply
  was run; production is unchanged.
- Full local Supabase stack integration tests (Auth + Storage services) were not
  run because those services are unhealthy in this Codespace.

### Production
- No production migration, hosted database operation, or unapproved path was
  performed or enabled.
