# Organization accounts, group workspaces, and access — closure (2026-11-09)

Status: **COMPLETE for the individual/organization account and organization
workspace/access path.** Production is live at
https://evidence-layer-gold.vercel.app. Enterprise-only features beyond this
scope (SSO, email invitations, billing, multi-organization membership) are not
part of this closure.

> **2026-11-11 addendum:** The deletion UI flag policy is now **off unless
> `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is exactly `true`**. This supersedes
> the 2026-11-09 “remains ON” operational decision while the worker deployment
> and a test-account accept-to-purged smoke are unverified. On 2026-11-11 the
> new worker and both smokes passed, and the production flag was re-enabled to
> `true`. See `docs/operations/DELETION-WORKER-VERIFY-2026-11-11.md`.

## Scope delivered

- Individual accounts start as `individual`; an organization account is created
  by upgrading the authenticated account with an organization name.
- The organization owner is the organization admin and can:
  - create group workspaces;
  - add an existing user by email with an `admin` or `member` role;
  - promote/demote a member;
  - remove a member;
  - delete a group workspace and its scoped content.
- Group workspace access is membership-based; group members see their group
  workspaces in `/v1/me` with the workspace role.
- Workspace & Access is rendered only for organization-type accounts.
- The owner cannot be removed or demoted from `admin`.

## Database and service

| Item | Status |
|---|---|
| Migrations applied | **35** (last: `20261109140000_group_workspace_deletion.sql`) |
| `accounts` type/provisioning | `20261109120000_account_type_and_organization.sql` |
| Organizations and memberships | `20261109130000_organizations_and_group_workspaces.sql` |
| Group workspace deletion | `20261109140000_group_workspace_deletion.sql` |
| RLS | self-read accounts/memberships/organizations; no client writes; definer RPCs only |
| Audit | `workspace_deleted` event retained with organization metadata |
| Storage | snapshot object keys queued to `data_deletion_storage_queue`; the deletion worker drains it |

Service endpoints:

- `POST /v1/me/organization` — upgrade the authenticated account.
- `GET /v1/organization` — organization overview (admin).
- `POST /v1/organization/workspaces` — create a group workspace.
- `POST /v1/organization/members` — add a member by email.
- `PATCH /v1/organization/members/{user_id}` — change a member role.
- `DELETE /v1/organization/members/{user_id}` — remove a member.
- `DELETE /v1/organization/workspaces/{workspace_id}` — delete a group workspace.

## Tests and live evidence

- Service and BFF tests: organization repository, routes, route inventory, web
  management UI, group workspace delete.
- pgTAP: organization creation, group workspace, member add/promote/remove,
  owner protections, RLS isolation, group workspace deletion.
- Guard: 74 tests; 602 statements / 516 registry-approved / 0 hard-denied.
- Web: 172 tests, typecheck and production build.
- Live synthetic smokes:
  - individual → organization; overview; group workspace; add member by email;
    member access; promote to admin; remove member; owner removal rejected 422.
  - group workspace delete: `workspace_deleted` audit row recorded, overview
    workspaces empty, the workspace and its memberships removed.
  - account deletion for the organization owner and member: `purged` and Auth
    404; the organization/group cascade on owner deletion.

## Limits and residual risks

- One organization per owner account; members must already have an account and
  are added by email (no invitation email delivery).
- The owner is the organization account; promoted admins can manage through the
  API, but Workspace & Access is shown in the UI only for organization-type
  accounts.
- The group workspace deletion smoke used an empty group workspace (no
  snapshots). Snapshot keys are queued and the existing deletion worker drains
  the queue; a snapshot-populated group workspace deletion should be included in
  any future Storage acceptance run.
- No enterprise SSO, organization billing, or multiple organization membership.
- The personal deletion flag was ON per the 2026-11-09 decision; the
  2026-11-11 addendum supersedes that default pending worker verification.
  The organization management UI is gated by account type.
- Supabase PITR remains off; the external R2 attestation is the recovery
  substitute (48-hour guard window).

## Outcome

**COMPLETE for the delivered organization path.** No production user data was
deleted during acceptance; all live smoke accounts were synthetic and were
purged through the deletion request/worker path.
