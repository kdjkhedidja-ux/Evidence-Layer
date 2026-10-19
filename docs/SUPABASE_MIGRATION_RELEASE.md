# Supabase migration release

GitHub Actions is the intended single production migration mechanism. The
Supabase GitHub integration remains disconnected, so it cannot independently
apply the same migration files. The existing `Evidence-test` project has ref
`ayrtyjgqlgtuiwuyigbz`. Do not create another project or deploy the local
`config.toml` to this project: its Auth URLs are local development settings.

## Current safety state

On 2026-10-19, authenticated Management API reads confirmed the project name,
ref, and healthy status. Its migration history table, user tables, indexes,
policies, triggers, and `vector` extension were absent. All four repository
migrations are pending. The backups API reported no completed backup and PITR
disabled; no usable recovery point was verified. GitHub's `Production`
environment had no protection rules or deployment branch policy. The available
GitHub token received HTTP 403 for branch protection, environment secret, and
environment variable APIs. Production planning and application jobs in the
workflow are therefore explicitly disabled.

## Gates before enabling hosted deployment

1. The repository owner must protect `main` and require the existing local
   migration, Auth, and RLS checks. In the private repository, verify the
   GitHub plan supports these controls.
2. Set `Production` to allow deployments only from `main`. Require a reviewer
   for each job; prevent self-approval and administrator bypass where supported.
   Verify these settings through GitHub before editing either disabled job.
3. Store `SUPABASE_ACCESS_TOKEN` and `SUPABASE_DB_PASSWORD` as `Production`
   environment secrets. Set `SUPABASE_PROJECT_REF` as an environment variable
   to the verified ref. Use a project-scoped token with only required rights.
   Codespaces secrets are not GitHub Actions secrets.
4. Verify a recent completed backup or PITR recovery point on the actual
   project. Decide whether its age and retention meet the release's recovery
   objective, and identify the owner who can authorize a restore. The guard
   refuses to plan without a recovery point within 48 hours.
5. After those controls are verified, a reviewed PR may remove the literal
   `false` from the two production job conditions. The `main` condition must
   remain. Run a manual plan first, inspect its JSON artifact and CLI dry-run,
   and verify that it targets the same commit, project, and migration versions.
   Enable automatic production application only after an end-to-end staging
   apply and post-apply verification succeed on an existing authorized staging
   target. No staging target was verified during this preparation.

## Release and recovery

The workflow's plan contains the exact commit, target identity, migration
filenames and SHA-256 hashes, remote applied versions, pending versions, and
the observed recovery point. The preflight compares remote history to the
reviewed migration prefix and checks the corresponding catalog state. The
second job waits for its own `Production` environment approval after the plan
artifact exists. Its approval request names the target ref and plan digest;
the reviewer must compare both with the same-run artifact. Immediately before
applying, it recomputes the plan and
requires an exact match. A new remote change, backup state, or migration file
changes the digest and blocks application. A manual apply additionally
requires the dispatcher to type the target ref. Automatic runs require the
reviewer to inspect the same-run artifact before approval.

After an apply, the guard requires all reviewed migration versions in remote
history and checks the expected tables, indexes, foreign keys, RLS flags,
policies, triggers, functions, and grants. A CLI schema diff must be empty.
The existing local CI also exercises the database, Auth, and workspace
isolation with synthetic users. Those tests must remain local; they create
test data and must not be pointed at production.

A failed migration can leave earlier migrations applied. Do not use
`supabase db reset --linked`, `migration repair`, or a restore as an automatic
response. For a reversible schema mistake, prepare and review a new forward
corrective migration. A database restore can lose writes made after the
recovery point, while reverting application code does not revert database
state. Only the project owner may authorize a restore after checking the
actual recovery point and expected data loss. The workflow never restores or
deletes production data.
