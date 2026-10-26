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

## Reviewed manifest and validation controls (2026-10-26)

`supabase/migrations.manifest.json` is the reviewed inventory: one entry per
migration with its `sha256` and a `provenance` note. `validate` only proves that
the directory matches that manifest and that the bytes are unchanged. It says
nothing about whether a migration is safe to deploy.

Static SQL review is a separate control (`classify`), and since 2026-10-27 it is
a real statement splitter rather than a line-oriented regex. It is still not a
PostgreSQL parser, and it does not try to be: it answers one question per
statement, "is this exact statement approved?", and it fails closed when it
cannot.

`scripts/sql_statement.py` lexes the migration text with a state machine that
understands line comments, nested block comments, single-quoted strings with
doubled quotes, `E'...'` escapes when the `E` is adjacent, quoted identifiers,
and dollar-quoted bodies carrying any tag. Every statement is then placed in one
of three tiers:

| Tier | Meaning | Control |
| --- | --- | --- |
| 0 | Never acceptable in a migration | Blocked outright; no ledger entry can approve it |
| 1 | Needs an explicit, written approval | Must appear in `supabase/migrations.statements.json` |
| 2 | Reviewed shape, no approval needed | `CREATE TABLE`/`INDEX`/`SCHEMA`/`EXTENSION`/`TRIGGER`/temp table, `COMMENT ON`, `SET`/`RESET`, `ANALYZE` |

Tier 0 covers `SET ROLE`, `DROP SCHEMA`, disabling or un-forcing row level
security, `SUPERUSER`/`BYPASSRLS`/`REPLICATION`, `GRANT ... TO PUBLIC`, and
`SECURITY DEFINER` without a pinned `SET search_path`. Anything the lexer cannot
close - an unterminated string, identifier, block comment or dollar-quoted body -
or cannot classify is a blocking failure, never a default allow.

The ledger keys each Tier 1 statement by `sha256(file + normalised statement)`,
so changing the object, the privilege, the grantee list or the statement order
inside a file invalidates the approval and forces a written rationale for the
new statement. Each entry points at a rationale in the same file that states
what the statement does and what independently pins it (the pgTAP policy
signature assertions, the ACL probes, the identity suite).

Known limits, recorded rather than hidden:

* a `DO` block body is opaque to the statement lexer. `DO` is therefore Tier 1
  and approved as a single unit, which is exactly why it is gated: it is the one
  construct that can wrap arbitrary statements behind an innocuous verb.
* the ledger is scoped per file, not per database object, so a reviewer must
  still read what the statement touches.
* nothing here inspects the remote project. `validate` and `classify` speak only
  about the reviewed bytes.

Release planning remains a distinct step. `plan` is read-only against the
project; `assert-plan` re-verifies that neither the reviewed bytes nor the remote
state changed; `verify` runs only after an approved apply.

As of 2026-10-26 the repository carries eleven migrations. The manifest
re-approves `20261019135502_personal_workspace_bootstrap.sql` explicitly after
its rename from `20261017100000_personal_workspace_bootstrap.sql`: the content is
byte-identical, but a matching hash proves identity, not that the earlier
approval still applies.

As of 2026-10-27 the statement ledger covers those eleven files: 191 top-level
statements, 155 of them Tier 1 with a written rationale and 0 Tier 0. The count
is a measured output of `classify`, not an estimate; re-run it after any
migration change.
