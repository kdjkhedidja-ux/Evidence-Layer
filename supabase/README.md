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
The local `config.toml` is only for the Supabase development stack and disables
signup, anonymous sign-in, Studio, and Storage. It does not configure or link a
remote project.

Run the RLS tests against a local database only:

```bash
supabase start
supabase db reset --local
supabase test db --local
supabase stop --no-backup
```

The tests create synthetic users and assert row visibility, write denial, and
personal-workspace idempotence while executing as Supabase's `authenticated`
and `anon` Postgres roles. They do not use production credentials or claim
that a hosted project's RLS setup has been verified.
