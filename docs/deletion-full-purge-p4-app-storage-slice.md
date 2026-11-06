# Full Purge P4: Application and Storage Boundary

Status date: 2026-11-05. P4 extends the bounded worker slice with only principal-owned application access rows. It does not claim full account purge or terminal `purged`.

## Ownership rules

The current schema does not provide a reliable personal-owner field for evidence: `evidence_records` has `workspace_id` but no `created_by` (`supabase/migrations/20261018150000_workspace_scoped_evidence_records.sql:4-15`). Evidence and relationships in a shared workspace are therefore not account-owned merely because the requestor is an owner or authored a relationship. P4 leaves them intact.

- **Evidence records and relationships:** do not delete from shared workspaces. Their ownership is workspace-scoped, and deleting them could destroy other active members' data. A future personal-data rule needs an explicit schema/design decision.
- **Workspace memberships:** delete only rows for the requesting principal where `role = 'member'`. This removes that principal's own access and does not alter the workspace owner, other members, or workspace content. Owner memberships remain for separate ownership resolution.
- **Personal workspaces:** no personal-workspace marker exists in the current table, and `ensure_personal_workspace` does not establish a purge-safe ownership contract. Workspace rows are not deleted.
- **Source grants:** delete the principal's grants as in P3. Membership FK cascades are not relied upon; grants are explicitly frozen and removed first.
- **Shared data:** owner/co-owner workspace rows, evidence, relationships, source catalog rows, and other members' memberships remain.

## Ordered P4 effects

At claim, `private.run_account_deletion_slice` freezes `data_deletion_work_set` entries containing only IDs and target types:

1. `source_grant` targets for the principal.
2. `workspace_membership` targets for the principal with `role = 'member'`.
3. Delete the exact frozen source-grant targets.
4. Delete the exact frozen member-membership targets.
5. Verify no frozen source-grant or member-membership target remains.

The work set is forced-RLS, worker-insert/select only, has stable target uniqueness, and has no user-content fields. `workset_frozen`, `purge_effect`, and `purge_verified` events record counts and verification without content. Any effect/verification exception marks the request and account `purge_failed` with a safe category. A successful P4 run remains `purge_pending`; no terminal state is set.

The migration and test are `supabase/migrations/20261105130000_account_application_purge_slice.sql` and `supabase/tests/account_application_purge.test.sql`. The shared-workspace test proves that the owner, another member, and evidence row remain after the requesting member's access rows are removed.

## Snapshot and Storage policy

P4 deliberately implements **no physical snapshot or Storage delete**. This is the safer choice under the current contracts:

- `source_snapshots` is immutable and rejects client/table deletes; it retains publication identity and verification metadata (`supabase/migrations/20261022100000_source_snapshot_catalog.sql:203-214,327-400`).
- `storage.objects` has only staged-owner INSERT and published-grant SELECT policies; no DELETE policy exists (`supabase/migrations/20261022110000_source_snapshot_storage_policies.sql:97-138`).
- The current account-root manifest does not contain exact staged object bindings, and the worker has no Storage capability. Adding a delete policy without an exact object work set and metadata probe would weaken the design's `NOBYPASSRLS` boundary.
- **Staged objects:** remain deferred, including orphan cleanup. A future staged purge must freeze exact catalog/object identities, drain upload/verification races, delete only those exact objects, and verify absence.
- **Published objects:** remain deferred. The immutable catalog row and publication history are retained; no published bytes are removed in P4. A future version/source purge needs its own approved scope, exact object binding, audit, absence probe, and retention/receipt decision.

No bucket-wide listing or delete capability was added.

## Tests and status

`supabase/tests/account_application_purge.test.sql` covers ordered work-set construction, source-grant and member-membership effects, shared-workspace safety, evidence retention, blocked lifecycle state, audit, and worker role login bounds. The existing worker test continues to cover double-claim rejection. Existing Storage policy coverage confirms authenticated DELETE is denied for staged and published objects (`tests/integration/test_local_storage_snapshot_policies.py:123-148,260-285`). Migration guard inventory remains required and clean locally; no production migration was applied.

P4 is **PASS for the implemented application-data slice only**: source grants and principal member memberships are purged and verified. P4b separately adds sole-owner evidence/relationship deletion. The combined work is not a pass for full purge or Storage/snapshot deletion; see `docs/deletion-full-purge-p4b-evidence-ownership.md`.

## P5 residual blockers

- Exact personal evidence/relationship ownership model and any safe personal-row purge.
- Personal-workspace identity/ownership marker and sole-owner resolution.
- Exact staged-object work set, worker Storage delete policy, metadata absence probe, and upload/publication race fencing.
- Published snapshot physical deletion policy, immutable publication receipt/retention, and cross-tenant key verification.
- Terminal `purged` transition only after every approved target and retained metadata/backup fact is independently verified.
- Provider-backed restore/PITR reconciliation, external journal acknowledgement, leases/retries, and production evidence.
