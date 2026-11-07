# Supabase

The local schema now includes the first SaaS identity boundary: Supabase Auth
users, workspaces, and workspace memberships guarded by Postgres RLS. Nothing
here is linked to a hosted Supabase project.

## Rules

- Schema changes are made only through versioned migrations in
  `supabase/migrations/`. Migrations are the source of truth.
- Never edit a production database by hand from Studio or the SQL editor.
- Planned flow: local CLI -> migration review -> staging -> production.
- Never commit a `.env` file, project URL with keys, or any secret. See
  [docs/ENVIRONMENT_AND_SECRETS.md](../docs/ENVIRONMENT_AND_SECRETS.md).

## Current state

`migrations/20261007170000_platform_baseline.sql` enables the `vector`
extension. `migrations/20261008000000_rls_auto_enable_prerequisite.sql` restores
the `rls_auto_enable()` event trigger that enables RLS on every new `public`
table. That restored body logged a failed `enable row level security` and
continued, so a table could be created without RLS while the statement that
created it succeeded. `migrations/20261028120000_rls_auto_enable_fail_closed.sql`
keeps the same function signature, owner, fixed `search_path`, ACL, and event
trigger, but re-raises with the table identity and the original `SQLSTATE`, and
checks `pg_class.relrowsecurity` after the `ALTER`. A public table can no longer
be created unprotected: the creating migration fails instead. The cost is
deliberate - a legitimate table whose RLS cannot be enabled now blocks the whole
statement rather than shipping without RLS.

`migrations/20261016100000_saas_identity_workspace_boundary.sql`
adds workspace and membership tables, grants, and default-deny RLS policies.
`migrations/20261017100000_personal_workspace_bootstrap.sql` adds one optional
personal-owner key per workspace and a narrowly granted
`ensure_personal_workspace()` function. The function takes no identity
arguments and derives the owner from the authenticated Supabase request.
The local `config.toml` enables email signup and confirmation with captured
test email so the full Auth flow can be exercised. It keeps anonymous sign-in
and Studio disabled and enables Storage only for the private `source-snapshots`
bucket created by `migrations/20261022110000_source_snapshot_storage_policies.sql`.
It does not configure or link a remote project.

`migrations/20261022100000_source_snapshot_catalog.sql` adds the snapshot
catalog (`source_snapshots`) and explicit per-source read grants
(`source_grants`). Clients can only stage a version. Publication goes through
the two RPCs of `migrations/20261022120000_snapshot_verifier_identity.sql`,
callable only by an Auth user listed on the workspace-scoped, empty-by-default
`private.snapshot_verifier_allowlist` for the snapshot's workspace; the older
direct-UPDATE route of the `snapshot_verifier` role is closed by that migration.
A publication must match the staged claim and the stored object (a trigger also
requires the uploaded object to exist with the declared size). Reading a
published version needs an active membership and a read grant; ownership
implies neither. The Storage policies allow INSERT and SELECT only, so
overwrite (`x-upsert`) and delete are denied.

Limits: **publication is closed by default.** The allowlist is empty and the
legacy `snapshot_verifier` role is unassignable to any API role and holds no
UPDATE privilege. A JWT naming a database role proves who called, not that an
object's SHA-256 was verified, so it is never treated as verification evidence.
PostgreSQL also cannot recompute an object's SHA-256: it enforces that the
caller is a listed verifier for that workspace, that the evidence matches the
staged claim and the Storage object's identity and size, not that the evidence
is honest. A dishonest or compromised verifier (or any RLS-bypassing
credential) can publish bytes that do not match the catalog digest; the read
path therefore re-verifies size and SHA-256 before returning bytes. An
unpublished object stays in Storage unreadable by clients; there is no
automatic cleanup. These migrations and tests have not been run against a
database yet.

Run the snapshot Storage checks against an isolated local stack only (they need
the stack's signing key and `supabase_db_*` container name, see the test
docstring): `tests/integration/test_local_storage_snapshot_policies.py`.

Run the RLS tests against a local database only:

```bash
supabase start
supabase db reset --local
supabase test db --local
(cd apps/web && npm ci && npm run test:integration:local)
supabase stop --no-backup
```

The tests create synthetic users and assert row visibility, write denial, and
personal-workspace idempotence while executing as Supabase's `authenticated`
and `anon` Postgres roles. They do not use production credentials or claim
that a hosted project's RLS setup has been verified.
The local Auth integration test uses a fresh synthetic email address and
Mailpit's captured messages to check signup, confirmation, password recovery,
sign-in, workspace provisioning, and cross-workspace evidence access. It reads
only CLI local-stack values and does not contact Resend or a hosted project.

The fail-closed path of the RLS bootstrap trigger is not reachable from pgTAP:
`postgres` owns every table the suite creates, so `enable row level security`
never fails there. It is reproducible only when the creating role is not the
event trigger's owner, which needs `set session authorization` as a superuser.
Run this against a disposable container only:

```bash
docker run --rm -d --name rls-probe -e POSTGRES_PASSWORD=pg \
  -e POSTGRES_HOST_AUTH_METHOD=trust supabase/postgres:17.11.0.004
docker cp supabase/migrations/20261008000000_rls_auto_enable_prerequisite.sql rls-probe:/tmp/p.sql
docker exec rls-probe psql -U postgres -v ON_ERROR_STOP=1 -f /tmp/p.sql
docker exec rls-probe psql -U supabase_admin -d postgres -c "create role probe nologin" \
  -c "grant create on schema public to probe" \
  -c "set session authorization probe" -c "create table public.leak (id integer)"
# baseline: the table exists and relrowsecurity is false; nothing was raised
docker exec rls-probe psql -U postgres -c "select relrowsecurity from pg_class where oid = 'public.leak'::regclass" \
  -c "drop table public.leak"
docker cp supabase/migrations/20261028120000_rls_auto_enable_fail_closed.sql rls-probe:/tmp/f.sql
docker exec rls-probe psql -U postgres -v ON_ERROR_STOP=1 -f /tmp/f.sql
docker exec rls-probe psql -U supabase_admin -d postgres -c "set session authorization probe" \
  -c "create table public.leak (id integer)"
# after the fix: error 42501 "must be owner of table leak", and no table remains
docker rm -f rls-probe
```

## Snapshot verifier administration and audit

The forward-only migration `20261023150000_snapshot_verifier_admin_audit_guards.sql`
defines separate `NOLOGIN` control roles: `snapshot_verifier_allowlist_owner`,
`snapshot_verifier_allowlist_manager`, and `snapshot_verifier_audit_owner`.
These names are local design constants following the existing role naming
convention. The migration does not assign a human or operator identity and does
not add an administrative RPC. The allowlist owner owns the allowlist and its
private policy baseline; the publisher function owner receives read access only.
The manager receives only column-level `SELECT`/`INSERT` on the user and
workspace keys plus `DELETE` on the allowlist, and `SELECT` on the audit log.
It receives no table ownership, DDL, `UPDATE`, or `TRUNCATE` rights. API,
verifier, publisher, and control roles remain separate, with no membership
assigned to an operator by this migration. Provisioning an individually
auditable manager identity requires a separately reviewed administrative step.

Allowlist `INSERT` and `DELETE` events are written by a private trigger into a
forced-RLS audit table. Its `session_user` field identifies the PostgreSQL
session login, not the human behind shared credentials or a connection pool.
The audit row is in the same database transaction as the allowlist change, so a
rollback also removes that event; failed attempts are not recorded there. Use
an externally controlled audit stream if the operator needs stronger human
attribution or records of failed attempts.

The migration requires the complete prior verifier guard state and aborts on
unknown policies, unexpected ownership/ACLs, or partial objects. It retains
`source_snapshots_published_verifier_binding` without running
`VALIDATE CONSTRAINT`; legacy published rows receive no invented binding. The
final ownership/grant/revoke sequence is inside one PostgreSQL `DO` statement,
but the complete migration file has not been proven atomic under the installed
Supabase migration runner. Earlier statements can leave private control objects
if a later statement fails. A retry can reuse safe `NOLOGIN` roles if those are
the only objects left; if helper objects were created, preflight deliberately
rejects that partial state for operator review rather than cleaning it
automatically. Before applying this migration, verify failure rollback at each
critical boundary using the actual local Supabase CLI version and PostgreSQL 17
in a disposable stack.

The Python migration guard checks below are source-text checks only. pgTAP
assertions need an isolated local database and are not evidence about Supabase
Storage API versioning or hosted project permissions. No migration or test in
this section is considered run until a separately approved local validation is
completed. Production remains outside this local workflow.
