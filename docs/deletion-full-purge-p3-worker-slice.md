# Full Purge P3: Bounded Worker Slice

Status date: 2026-11-05. P3 implemented the bounded worker foundation; P4 extends it with principal-member membership removal. The combined slice remains non-terminal and does not delete workspaces, evidence, relationships, snapshots, Storage objects, or Auth identities.

## Privilege model

`account_deletion_worker` is a dedicated `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS` role created by `supabase/migrations/20261105120000_account_deletion_worker_slice.sql`. The migration asserts those attributes and rejects inherited role membership. The role has only the privileges needed by the implemented path:

- read and narrow status/claim updates on `data_deletion_requests`
- bound lifecycle updates for the request's account
- select/delete on `source_grants`, constrained by RLS to the request-bound principal
- select/delete on principal `member` memberships only, constrained by the request-bound principal
- insert on the existing content-free deletion audit table
- execute on the private worker function

It has no evidence-row SELECT privilege, no generic SQL/admin privilege, no Storage privilege, no snapshot privilege, and no browser/MCP credential. The worker entrypoint is `services/evidence-service/evidence_service/purge_worker.py`; it requires an operator-managed `EVIDENCE_PURGE_DATABASE_URL` for a dedicated worker connection and is not imported by the HTTP application. User request paths continue using verified user tokens and never use `service_role`.

## Claim, effect, and verify

`private.run_account_deletion_slice(request_id)` is the only execution function. It is owned by the worker role and is callable only by that role.

1. **Claim:** lock one request row and require status `accepted`. The function sets a transaction-local request binding, generates a claim ID, changes the request to `purge_pending`, sets `claimed_at`, and transitions account lifecycle `revocation_pending -> purge_pending`. A second concurrent or replay claim sees a non-`accepted` request and fails with `55000`; it cannot create a second claim.
2. **Effect:** P4 freezes and deletes only source grants and memberships where the accepted principal is a `member`; owner memberships, evidence, relationships, workspaces, source snapshots, and object bytes remain. Exact IDs are recorded in the worker-owned work set.
3. **Verify:** count remaining frozen source-grant and member-membership targets. Zero is required. Success writes `purge_claimed`, `workset_frozen`, `purge_effect`, and `purge_verified` audit events and leaves the request/account at `purge_pending`, because this is not a complete account purge.
4. **Failure:** effect or verification errors are caught inside the worker slice, the request becomes `purge_failed`, account lifecycle becomes `purge_failed`, and a content-free `purge_failed` event records `failure_category=verification_failed`. No success or terminal purge claim is returned.

The lifecycle trigger permits the worker only `revocation_pending -> purge_pending|purge_failed`; the pre-worker `available -> revocation_pending` transition remains controlled by the P1 acceptance function. Later `purge_pending -> purged` transitions are not enabled by this slice.

## Why source grants are the first effect

Source grants are an application access-control table and can be removed without deleting shared workspace membership, immutable publication metadata, or physical bytes. Removing them first reduces account access while preserving the design's rule that account deletion does not cascade into a workspace owned or co-owned by another user. The worker's RLS binding requires the transaction-local request ID to resolve to an accepted/purge-pending request and the exact principal scope; it cannot select unrelated evidence tenants or choose a bucket/object path.

## Tests and deferred P4/P5 work

`supabase/tests/account_deletion_worker.test.sql` covers worker role attributes, unrelated evidence SELECT denial, source-grant DELETE capability, accepted claim/effect/verification, lifecycle/request intermediate states, audit events, and double-claim failure. `supabase/tests/account_application_purge.test.sql` covers work-set ordering and shared-workspace safety. `tests/test_purge_worker.py` verifies the CLI refuses to run without its private worker credential. Migration guard validation remains clean.

P4 must still implement and review:

- complete account target enumeration beyond the account-root manifest
- evidence/relationship and personal-workspace effects only where ownership/shared-data rules are explicit
- physical staged/published Storage and snapshot handling
- independent journal acknowledgement and accepted-receipt reconciliation
- bounded worker leases/retries and production observability
- provider-backed restore/PITR reconciliation and completion proof
