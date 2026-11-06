# Full Purge P4b: Evidence Ownership

Status date: 2026-11-05. P4b extends P4 with a conservative ownership rule for application evidence. It does not delete owner memberships, workspaces, snapshots, Storage objects, Auth identities, or shared-workspace data.

## Ownership rules

The current `evidence_records` table has no `created_by`; its ownership signal is only `workspace_id`. A workspace is eligible only when the request principal is its sole active owner and there are zero other active `member` rows. The worker resolves this under a security-definer eligibility predicate and freezes exact IDs before any delete.

- Evidence in an eligible sole-owner/no-active-member workspace may be deleted.
- Evidence in any shared workspace remains untouched, even when the request principal is an owner or authored related data.
- Relationships in eligible workspaces are frozen and deleted explicitly before evidence. Relationship FK cascades are not relied on for the worker effect; direct deletes preserve the existing relationship audit trigger.
- Owner memberships are never deleted. A sole owner with no eligible successor is an ownership-resolution condition, not permission to erase the workspace.
- Workspace rows and personal-workspace rows remain because the schema has no reviewed personal-workspace marker or safe account-purge contract.
- Other users' memberships, shared evidence, and shared relationships remain.

## Ordered work set and effects

`data_deletion_work_set` now carries an immutable `target_id` and supports these type/id-only targets:

1. `source_grant` targets from P4.
2. Principal `workspace_membership` targets where role is `member`.
3. `relationship` targets in eligible sole-owner workspaces.
4. `evidence_record` targets in eligible sole-owner workspaces.

The worker freezes all four categories before effects. It then deletes source grants, member memberships, relationships, and evidence in that order. Evidence/relationship worker policies use the same sole-owner/no-active-member predicate, so a stale or fabricated work-set target cannot widen authority. Verification requires every frozen target to be absent. Any exception marks the request and account `purge_failed` and writes a safe failure category.

The owner membership and workspace remain, and the request/lifecycle remain `purge_pending` after successful verification. No terminal `purged` state is claimed.

## Snapshot and Storage boundary

P4b deliberately keeps both staged and published physical object deletion **DEFERRED**. The current catalog is immutable, the current Storage policies have no API-role DELETE policy, and the manifest/work set does not yet contain exact trusted object bindings plus a provider metadata absence probe. No Storage privilege, bucket listing, bucket-wide delete, or published-object delete was added. A later slice must freeze exact staged object identities, drain upload/publication races, delete only those keys, verify absence, and separately decide the immutable published metadata/receipt policy.

## Tests and validation

`supabase/tests/account_evidence_ownership.test.sql` covers sole-owner evidence/relationship deletion, shared-workspace evidence retention, owner-membership retention, exact work-set targets, non-terminal state, and audit. `supabase/tests/account_application_purge.test.sql` covers P4 grant/member effects and shared workspace safety. Existing Storage integration tests continue to prove staged and published authenticated DELETE denial (`tests/integration/test_local_storage_snapshot_policies.py:123-148,260-285`).

Migration review remains clean locally: 403 statements across 20 files, 330 registry-approved, and zero hard-denied statements. No production migration was applied.

## P5 residual blockers

- Reviewed personal-workspace marker and ownership/sole-owner resolution for workspace metadata.
- Any additional personal evidence ownership model not expressible by sole-owner workspace scope.
- Exact staged-object work set, worker-only Storage delete policy, upload/publication fencing, and absence verification.
- Published snapshot physical purge, immutable publication receipt, metadata retention, and cross-tenant key proof.
- External journal acknowledgement, worker leases/retries, terminal `purged` transition, and full completion proof.
- Provider-backed PITR/restore reconciliation and production evidence.
