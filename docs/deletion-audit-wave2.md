# Deletion Audit Wave 2

Wave 2 covers only the existing workspace-scoped evidence and relationship DELETE paths. It does not add account, workspace, source, snapshot, Storage, worker, or JEV deletion behavior.

## Store and write path

`public.evidence_deletion_audit` is a separate, forced-RLS table for content-free successful DELETE events. It stores the server timestamp, `auth.uid()` actor, workspace, resource type/id, fixed `delete` action, fixed `success` result, and compact metadata. The migration is `supabase/migrations/20261105100000_evidence_deletion_audit.sql`.

The existing `DELETE /v1/workspaces/{workspace_id}/evidence/{evidence_id}` and relationship DELETE handlers continue to use the caller's verified Supabase JWT. Database `AFTER DELETE` triggers write the audit row in the same transaction, so an audit failure rolls back the data delete and no HTTP 204 is produced. Evidence FK-cascade removal of relationships does not create a second relationship event; a direct relationship DELETE records its relationship type only.

Authenticated clients have no audit table DML privileges and explicit deny policies. Mutation triggers reject UPDATE and DELETE, making the audit store append-only for the exposed roles. No evidence text, relationship rationale, passage body, token, or secret is written to metadata.

## Tests and non-goals

`supabase/tests/evidence_deletion_audit.test.sql` verifies successful evidence and relationship events, actor/workspace/resource binding, cascade de-duplication, cross-workspace denial without an audit event, and authenticated UPDATE/DELETE denial.

This wave does not implement `revocation_pending` or an in-progress/cancel guard, source or published snapshot purge, workspace/account deletion, a deletion worker, independent external journal durability, or restore-drill integration. Those remain outside this slice.
