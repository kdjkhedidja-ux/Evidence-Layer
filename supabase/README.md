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
extension. `migrations/20261016100000_saas_identity_workspace_boundary.sql`
adds workspace and membership tables, grants, and default-deny RLS policies.
`migrations/20261017100000_personal_workspace_bootstrap.sql` adds one optional
personal-owner key per workspace and a narrowly granted
`ensure_personal_workspace()` function. The function takes no identity
arguments and derives the owner from the authenticated Supabase request.
The local `config.toml` enables email signup and confirmation with captured
test email so the full Auth flow can be exercised. It keeps anonymous sign-in,
Studio, and Storage disabled. It does not configure or link a remote project.

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
