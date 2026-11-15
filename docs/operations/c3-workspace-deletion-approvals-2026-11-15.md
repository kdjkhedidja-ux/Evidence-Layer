# C3 shared-workspace deletion approvals — 2026-11-15

Status: **implemented; migration 52 pending production apply.**

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

## Residual

- Execution is a hard delete of the workspace and its snapshots; source objects
  are queued for Storage cleanup.
- Stale requests require a new request.
