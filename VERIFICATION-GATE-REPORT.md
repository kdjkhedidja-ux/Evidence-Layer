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

## Gate 2 — Security migrations — IN PROGRESS (code fix applied; run pending)

- 12 migrations are listed in `supabase/migrations.manifest.json` (with sha256
  integrity hashes).
- Blocker fixed: `CREATE ROLE snapshot_verifier` and `CREATE ROLE
  snapshot_publisher_owner` were written inside PL/pgSQL `DO` blocks as direct
  statements (not permitted in a procedural block). Both are now wrapped in
  `EXECUTE` in `20261022100000_source_snapshot_catalog.sql` and
  `20261022120000_snapshot_verifier_identity.sql`. The manifest sha256 and the
  two statement-ledger entries were regenerated to match; the guard suite and
  static content suites still pass.
- NOT yet done: running all migrations in order on an isolated PostgreSQL and
  keeping the DENY_ALL default until safe read grants are proven (the actual
  Gate 2 acceptance criterion).

## Gate 3 — Database (pgTAP) tests — NOT RUN

- 7 pgTAP files under `supabase/tests/` are pending execution in an isolated
  environment:
  - `evidence_records.test.sql`
  - `personal_workspace.test.sql`
  - `rls_auto_enable.test.sql`
  - `snapshot_verifier_identity.test.sql`
  - `source_snapshot_storage.test.sql`
  - `source_snapshots.test.sql`
  - `workspace_boundary.test.sql`
- No pass/fail claim is made until they run.

## Gate 4 — Final review and approval — PENDING

- Production is unchanged; no unapproved path has been enabled.
