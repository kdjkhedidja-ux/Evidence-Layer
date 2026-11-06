# Full Purge P2: Access Guard

Status date: 2026-11-05. P2 verifies the account-scoped access fence after P1 acceptance. It does not add a workspace lifecycle state, purge execution, physical deletion, external journal acknowledgement, or production restore behavior.

## Gate coverage

The existing `_with_account_gate` performs a fresh Recovery Fence check followed by an authoritative `account_lifecycle` read. Only `available` proceeds. A blocked, unknown, missing, or unreadable lifecycle state denies before downstream access; source content also rechecks before response delivery (`services/evidence-service/evidence_service/app.py:243-289,303-318,614-650`).

The protected `/v1` route surface is:

- `GET /v1/me`
- `PUT /v1/me/personal-workspace`
- `GET /v1/workspaces/{workspace_id}`
- `GET /v1/workspaces/{workspace_id}/sources`
- `GET|POST /v1/workspaces/{workspace_id}/evidence`
- `GET|PATCH|DELETE /v1/workspaces/{workspace_id}/evidence/{evidence_id}`
- `GET|POST /v1/workspaces/{workspace_id}/relationships`
- `PATCH|DELETE /v1/workspaces/{workspace_id}/relationships/{relationship_id}`
- `GET /v1/workspaces/{workspace_id}/sources/{source_id}/versions/{version_id}/content`

The route matrix in `tests/test_evidence_service_account_lifecycle.py:430-484` sends every listed read and write route through `revocation_pending` and asserts 403 with no directory, evidence/relationship repository, catalog, or Storage read calls. The same test module covers `revoked`, `purge_pending`, `purge_failed`, `purged`, missing, and unknown lifecycle values (`:301-328`).

## Control-route exceptions

Only the following deletion control operations remain reachable:

- `GET /v1/deletion-requests/{request_id}` verifies identity and requester ownership through the repository/RLS query, and returns only the caller's status. It does not call workspace, evidence, relationship, catalog, or Storage paths.
- `POST /v1/deletion-requests/{request_id}/accept` is the narrow acceptance/replay control operation. The database function verifies request ownership, performs no purge, and replay returns the existing result without changing the manifest. It remains callable after local denial solely to complete an idempotent control replay.

`POST /v1/deletion-requests` is account-gated and cannot create a new request once the account is blocked. `/healthz` remains the only unauthenticated operational endpoint.

## Account versus workspace scope

P2 intentionally does **not** add `workspace_lifecycle`. The design explicitly separates account-data deletion from shared-workspace deletion: account deletion never cascades into a workspace owned or co-owned by another user, and other members retain shared-workspace access (`docs/data-control-phase8-design.md:164-178,180-187`). The P1 request is account-scoped, and its acceptance sets only the requesting principal's `account_lifecycle` row to `revocation_pending`. Therefore:

- The affected principal cannot use any current data route in any workspace, including workspaces where they are a member or owner.
- Other members' accounts remain `available` and their access to shared workspaces is not changed by this account request.
- A future workspace deletion request requires its own owner unanimity, inventory revision, workspace fence, and `workspace_lifecycle` transition. It is not inferred from this account request (`docs/data-control-phase8-design.md:184-196,367-394`).

This is an account-only decision, not a claim that workspace purge semantics are complete.

## Transition hygiene

Before a worker exists, the only permitted lifecycle transition is `available -> revocation_pending` with a non-null request reference. The account migration trigger rejects invalid state transitions, missing request references, caller-supplied versions/actors, and direct authenticated lifecycle DML (`supabase/tests/account_lifecycle.test.sql:202-277`). `revocation_pending -> revoked`, `revoked -> purge_pending`, and `purge_pending -> purged` remain worker/P2+ transitions and are intentionally not enabled or executed here.

## Residual P3 blockers

- Implement and review the bounded purge worker and its claim/effect/verification protocol.
- Resolve external journal acknowledgement and reconcile `journal_ack_pending` with an accepted receipt.
- Define complete account target enumeration and physical purge ordering without widening the P1 account-root manifest implicitly.
- Design the separate workspace lifecycle/approval path if workspace deletion enters scope.
- Add provider-backed restore/PITR reconciliation and production evidence.
