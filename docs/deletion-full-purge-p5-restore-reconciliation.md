# Full Purge P5: Local Restore Reconciliation

Status date: 2026-11-05. P5 establishes a local, database-only reconciliation property for the targets already handled by the account purge worker. It does not perform a provider PITR/restore, connect an external journal, delete Storage objects, or transition an account to terminal `purged`.

## Threat and consistency model

### A. Backup before acceptance

A restore from a backup taken before acceptance may contain no deletion request, an `available` lifecycle row, grants, memberships, and evidence. That is an expected historical state, not proof that a later deletion never occurred. The product rule is **re-request or remain blocked after authoritative recovery reconciliation**: an independent journal/recovery authority must establish whether a later accepted request exists before the service opens the restored database. If the external history is unavailable or ambiguous, the Recovery Fence stays closed. P5 does not implement that external authority.

### B. Backup after acceptance but during `purge_pending`

The restored lifecycle/request rows should remain `revocation_pending`/`purge_pending` and the service gate denies all protected data routes. If the restore contains partial application effects, the account remains denied. The worker-only `reconcile_account_deletion_slice` checks every frozen work-set target; a resurrected grant, member membership, relationship, or evidence row changes the request and lifecycle to `purge_failed` and records `reconciliation_failed`. It never changes the account to `available`.

### C. Backup after successful slice verification

A restore can resurrect a row even when the source database had verified it absent. The immutable work set remains the local target authority. P5 reconciliation rechecks exact target IDs and refuses to treat the account as clean when any target is present. The account stays blocked and becomes `purge_failed` for operator/retry handling. A clean local check records `reconciliation_verified` but leaves the account at `purge_pending`; it does not claim terminal purge.

## Recovery Fence and lifecycle linkage

Protected Evidence Service requests already perform a fresh Recovery Fence check followed by a fresh account lifecycle read (`services/evidence-service/evidence_service/app.py:264-300`). Any unavailable/closed/unknown Recovery Fence or any lifecycle state other than `available` fails closed. Source retrieval repeats the lifecycle check before open and delivery (`services/evidence-service/evidence_service/secure_snapshot_read.py:460-503`).

The local reconciliation function is `private.reconcile_account_deletion_slice(request_id)` in `supabase/migrations/20261105150000_deletion_restore_reconciliation.sql`. It runs only as `account_deletion_worker`, locks a `purge_pending` request, checks the immutable work set, audits a clean or restored-target result, and transitions only to `purge_failed` on inconsistency. It has no operation that opens an account. The Recovery Fence remains the outer serving boundary during restore/reconciliation.

This is local linkage, not the C0 independent journal integration. ADR 0007 and the Recovery Fence closure explicitly state that local replay is not a provider restore proof and that C0 deletion-journal events remain unsatisfied (`docs/adr/0007-independent-recovery-ledger.md:95-107,115-126`; `docs/phase8-recovery-fence-journal-closure.md:15-27,64-76`).

## Automated local test

`supabase/tests/deletion_restore_reconciliation.test.sql` seeds a principal, sole-owner workspace, evidence, and accepted request; runs the worker slice; verifies the target is gone and the lifecycle is blocked; records a clean reconciliation; then reinserts the purged evidence row as local test data to simulate a stale restore. The second reconciliation returns `purge_failed`, marks both request and lifecycle blocked/failing, and writes a content-free failure event. It does not contact a production project or provider.

`RESTORE-DRILL-REPORT.md:4-7,71-83` remains the only restore report: it proves an isolated PostgreSQL restore invocation, not Supabase Auth/Storage behavior, source equivalence, or deletion-journal reconciliation. P5 does not expand that claim.

## Guarantees today

- A restored `purge_pending`/`purge_failed` account cannot pass the service account gate.
- Exact P4b work-set targets are checked rather than inferred from broad account queries.
- A resurrected target is detected and recorded as a safe failure category without user content.
- A clean local reconciliation does not reopen access or claim `purged`.
- No user request path gains worker privileges, `service_role`, or Storage delete capability.

## P6 residual blockers

- Independent journal/recovery-authority linkage and durable accepted/completed event reconciliation.
- Provider-backed PITR/restore drill with Auth, Storage, object, cache, and old-link checks.
- Exact staged/published snapshot work sets and physical Storage policy/absence verification.
- Owner/workspace retention resolution and any remaining personal-data target enumeration.
- Retry/lease/observability policy for failed reconciliation and worker execution.
- Terminal `purged` transition only after the full approved target set, retained metadata, backups, and completion evidence are verified.
