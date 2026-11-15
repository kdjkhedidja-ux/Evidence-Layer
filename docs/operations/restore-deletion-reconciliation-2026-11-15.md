# Restore and deletion reconciliation — 2026-11-15

Status: **verifier implemented; provider backup/PITR decision still pending.**
This document records the P0.3 code-feasible increment. It does not enable
PITR, create a backup, run a provider restore, or change production.

## Scope

The verifier answers one question on a disposable restore target: does the
restored database still contain data for an account whose deletion request is
terminal `purged`?

It is read-only and checks:

- account-owned workspaces (personal, organization-owned, or sole-active-owner);
- remaining workspace memberships for the subject;
- remaining `source_grants`;
- evidence and source snapshots in account-owned workspaces;
- pending Storage-cleanup queue rows for those workspaces;
- `auth.users` identity when the restored database includes an `auth` schema.

It does not prove Storage object absence, provider backup deletion, or Auth
provider state when the `auth` schema is outside the restore. Those remain
provider/drill checks.

## Usage

Point the verifier at a disposable restore only:

```text
PYTHONPATH=services/evidence-service \
RESTORE_VERIFY_DATABASE_URL=postgresql://.../restored \
python -m evidence_service.restore_reconciliation
```

Optional: `RESTORE_VERIFY_LIMIT` (default 1000). Exit codes: `0` clean, `2`
resurrected data, `1` check failed. The variable deliberately has no
`DATABASE_URL` fallback so an operator cannot run it against production by
accident.

## Provider facts and proposed targets

Current observed state (2026-11-11, unchanged by this record):

- provider PITR: **disabled**;
- provider-listed backups: **0**;
- logical dump in private R2, excluding `auth`, `storage`, and `extensions`;
- local logical restore drill passed; no provider restore proof.

Proposed targets for operator sign-off (not yet committed):

| Target | Proposal | Rationale |
|---|---|---|
| RPO | 24 hours for the logical dump; 15 minutes only if PITR is enabled | The current artifact is a daily-style dump, not PITR |
| RTO | 4 hours for schema/data restore into a disposable target | Matches the existing drill path |
| Retention | 14 days for dumps; provider retention per plan | Keeps a rollback aid without unbounded copies |
| Deletion boundary | no resurrected `purged` account after restore reconciliation | Enforced by the verifier plus Recovery Fence |

## Drill procedure

1. Restore the selected artifact into a disposable PostgreSQL target.
2. Run the verifier above with `RESTORE_VERIFY_DATABASE_URL` pointing at that
   target.
3. If `status=resurrected`, keep the Recovery Fence closed, keep the account
   denied, and follow the operator reconciliation runbook. Do not reopen access.
4. If `status=clean`, record the artifact checksum, restore target, verifier
   JSON, and operator. Clean means only that the checked database rows are
   absent; it does not prove provider Storage/Auth absence.
5. Re-run after any PITR/managed-backup change or schema migration.

## Remaining P0.3 decision

The operator must choose one of:

1. enable provider PITR (and accept its cost/retention);
2. schedule managed provider backups with a documented retention; or
3. keep the logical-dump-only path and publish the limitation.

Until that decision and a fresh drill exist, P0.3 remains open even though the
verifier is implemented.
