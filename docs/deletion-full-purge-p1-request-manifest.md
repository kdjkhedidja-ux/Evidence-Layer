# Full Purge P1: Deletion Request and Target Manifest

Status date: 2026-11-05. P1 prepares an account-scoped full-purge request without executing purge. It adds no worker, Storage deletion, published-snapshot deletion, external journal, PITR operation, JEV, billing, or connector behavior.

## State machine

The design documents define account scope as the narrowest supported deletion scope. Workspace-specific lifecycle state is not installed, so P1 does not invent workspace requests or infer workspace deletion from an account request.

The request state machine is:

```text
approval_pending --authenticated acceptance--> journal_ack_pending
                                      \------> (accepted only after a future external journal acknowledgement)
```

`journal_ack_pending` is the local post-acceptance state because P1 does not implement the independent journal acknowledgement described by the data-control design. The acceptance transaction also moves the existing `account_lifecycle` row from `available` to `revocation_pending`. All protected data routes therefore remain denied. There is no cancel transition after acceptance.

The existing lifecycle labels remain unchanged: `available`, `revocation_pending`, `revoked`, `purge_pending`, `purge_failed`, and `purged` (`supabase/migrations/20261102120000_account_lifecycle.sql:43-84`).

## Request and immutable manifest

`public.data_deletion_requests` (`supabase/migrations/20261105110000_deletion_request_manifest.sql`) stores:

- `request_id`, requester principal, account scope type/id
- `approval_pending` or local `journal_ack_pending`/future `accepted` status
- server-created and accepted timestamps
- caller-provided idempotency key, unique per principal

The insert trigger overwrites principal, account scope, status, and timestamps from the verified `auth.uid()` context. Authenticated clients can insert and read only their own request rows; they cannot update or delete them directly.

`public.data_deletion_manifest` stores only immutable target references: one `target_type = account` and the requesting account's `target_id` for this narrow P1. No evidence text, passage body, relationship rationale, source content, Storage key, token, or signed URL is copied into the manifest. Forced RLS, no authenticated insert/update/delete privilege, unique request/target constraints, and an append-only mutation trigger prevent post-acceptance changes.

The deliberately narrow account-root manifest is a readiness boundary, not a claim that all future account-owned rows or shared workspaces may be purged. P2 confirms that this account request does not require a parallel workspace state; any future workspace deletion still requires its own reviewed lifecycle and ownership model.

## Idempotency and acceptance

`(requester_user_id, idempotency_key)` is unique, and one open request is allowed per principal/account scope through `data_deletion_requests_open_scope_key`. Re-submitting the same create key returns the existing request through the service repository's conflict-recovery lookup. Calling `public.accept_account_deletion(request_id)` for an already accepted/local-journal-pending request returns the existing row without changing lifecycle or inserting another manifest.

The acceptance function is a `SECURITY DEFINER` function owned by `account_lifecycle_transition_writer`, exposed only as the authenticated RPC needed by the service. It requires `auth.uid()` to own the request, updates the request, transitions the existing lifecycle row, inserts the manifest, and writes the acceptance audit event in one database transaction. No user request path uses `service_role` or `BYPASSRLS`.

## API

The Evidence Service exposes the minimum account-scoped control surface:

- `POST /v1/deletion-requests` with `{ "idempotency_key": "..." }` creates or replays the caller's own `approval_pending` request. It uses the normal verified-user authentication and account gate.
- `POST /v1/deletion-requests/{request_id}/accept` accepts the caller's own request. It is a control operation and remains replayable after the account becomes blocked so the same result can be returned idempotently.
- `GET /v1/deletion-requests/{request_id}` returns only the caller's own status. This is the intentionally narrow control/status exception that remains readable while data access is blocked.

The request/manifest migration and API repository are `supabase/migrations/20261105110000_deletion_request_manifest.sql` and `services/evidence-service/evidence_service/deletion_repository.py`; route wiring is in `services/evidence-service/evidence_service/app.py`.

## Audit events

P1 extends `public.evidence_deletion_audit` rather than creating a parallel audit store. It records `request_created`, `request_accepted`, and `manifest_frozen` events with the verified actor, request id, account scope, server result, and compact type/ordinal metadata. Existing evidence/relationship delete events remain unchanged. Audit rows remain forced-RLS, append-only, and unavailable for authenticated UPDATE/DELETE.

## Tests and non-goals

`supabase/tests/deletion_request_manifest.test.sql` covers forced RLS, account scoping, open-request uniqueness, authenticated mutation denial, request creation, lifecycle transition, manifest freezing, acceptance replay, and the three audit events. `tests/test_evidence_service_account_lifecycle.py` covers service create/accept/status wiring and confirms that status access does not bypass the pending-account data gate. Migration review is covered by the guard manifest/ledger/catalog updates and `tests/test_supabase_migration_guard.py`.

P1 does not execute purge, delete any physical object, add a worker, acknowledge an external journal, add workspace lifecycle transitions, or prove production restore consistency.

## P2 residual blockers

- Review and implement workspace-specific lifecycle/ownership semantics before expanding beyond the account-root manifest.
- Define the complete account target enumeration, including shared-workspace and source-retention treatment, without copying user content.
- Add the independently durable journal/acknowledgement boundary and reconcile it with `journal_ack_pending`.
- Design and review a bounded purge worker, then add execution/idempotency proof without `service_role`, `BYPASSRLS`, or bucket-wide deletion.
- Add provider-backed restore/PITR reconciliation tied to the frozen manifest and audit history.
- Install the Python service test dependencies and execute the HTTP suite in a controlled local environment.
