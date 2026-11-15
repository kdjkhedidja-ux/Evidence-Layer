# C3 shared-workspace deletion approvals — 2026-11-15

Status: **implemented and production-verified.**

## Decision

Organization admins are the workspace owners for group-workspace deletion.
Deletion requires the unanimous approval of every active organization admin in
the revision captured when the request is created.

## Mechanics

- `request_workspace_deletion` snapshots the admin set and auto-approves the
  requester.
- `approve_workspace_deletion` records each admin; unanimity marks the request
  approved.
- `withdraw_workspace_deletion_approval` returns an approved request to pending.
- `cancel_workspace_deletion` cancels a pending/approved request.
- Any owner-set change invalidates the snapshot; the next approval marks it
  stale.
- Approved requests execute after a 24-hour withdrawal window through the
  privileged deletion worker.
- Direct `delete_group_workspace` is revoked from authenticated clients.

## Production verification

- Migration 52 applied and verified (52 migrations, pending=0).
- Evidence Service deployment: `8770b42b-1bd4-4d05-b240-3eba83a879b3`.
- Deletion worker deployment: `33195e46-b44e-456f-8cfc-ea12020c957d`.
- Two synthetic organization admins requested and approved unanimously:
  - request status `pending`, owner_count `2`;
  - approval status `approved`, approved_count `2`;
  - after the due window, workspace `0`, request `0`, audit `workspace_deleted` `1`.

## Residual

- Execution is a hard delete of the workspace and its snapshots; source objects
  are queued for Storage cleanup.
- Stale requests require a new request.
