# Current Deletion-Phase Inventory

This is an inventory of behavior present in the repository as inspected on 2026-11-05. It does not implement or approve a deletion workflow, and it does not claim phase closure.

The formal scope closure and frozen boundary are recorded in [`docs/DELETION-PHASE-CLOSURE.md`](DELETION-PHASE-CLOSURE.md).
The P1 request/manifest extension is recorded in [`docs/deletion-full-purge-p1-request-manifest.md`](deletion-full-purge-p1-request-manifest.md).
The P2 access decision and route coverage are recorded in [`docs/deletion-full-purge-p2-access-guard.md`](deletion-full-purge-p2-access-guard.md).
The P3 bounded worker slice is recorded in [`docs/deletion-full-purge-p3-worker-slice.md`](deletion-full-purge-p3-worker-slice.md).
The P4 application/storage boundary is recorded in [`docs/deletion-full-purge-p4-app-storage-slice.md`](deletion-full-purge-p4-app-storage-slice.md).
The P4b evidence ownership extension is recorded in [`docs/deletion-full-purge-p4b-evidence-ownership.md`](deletion-full-purge-p4b-evidence-ownership.md).
The P5 local restore reconciliation contract is recorded in [`docs/deletion-full-purge-p5-restore-reconciliation.md`](deletion-full-purge-p5-restore-reconciliation.md).
The final P0–P5 full-purge closure and production boundary are recorded in [`docs/DELETION-FULL-PURGE-CLOSURE.md`](DELETION-FULL-PURGE-CLOSURE.md).
The next staging-readiness checklist is recorded in [`docs/deletion-full-purge-p7-staging-readiness.md`](deletion-full-purge-p7-staging-readiness.md).
The 2026-11-05 read-only staging preflight is recorded in [`docs/deletion-full-purge-p7-staging-preflight-2026-11-05.md`](deletion-full-purge-p7-staging-preflight-2026-11-05.md).

## Summary table

| Goal | Current status | Evidence |
|---|---|---|
| Scoped deletion | PARTIAL | Evidence/relationship DELETEs are workspace-filtered and owner-gated in `services/evidence-service/evidence_service/app.py:874-1023` and by RLS policies in `20261101130000_workspace_evidence_relationships.sql`; no scoped account/source/workspace purge API exists (`docs/data-control-phase8-design.md:28-39`). |
| Deletion proof + independent audit log | PARTIAL | Existing evidence and relationship DELETEs now write transaction-bound, content-free events to `public.evidence_deletion_audit` via `20261105100000_evidence_deletion_audit.sql`; the store is database-local, and an external journal is deferred optional hardening (ADR 0009), not a Phase 8 exit gate. |
| Block access during cancel/deletion | PARTIAL | Installed account lifecycle gating reads `account_lifecycle` on every protected `/v1` request and denies every non-`available` state before data access (`services/evidence-service/evidence_service/app.py:243-289`, `20261102120000_account_lifecycle.sql:43-145`); workspace-specific lifecycle state and a revocation transition remain absent. |
| Consistency after restore | PARTIAL | Local Recovery Fence/replay protocol and tests exist, but the documents state that C0 deletion-journal integration and a real restore/PITR drill are not implemented (`docs/adr/0007-independent-recovery-ledger.md:105-125`, `docs/phase8-recovery-fence-journal-closure.md:64-82`). C0 external journal is deferred optional hardening (ADR 0009), not a Phase 8 exit gate; provider PITR proof remains open. |
| Worker privilege bounds | PARTIAL | Current request paths use verified user tokens and reject service-role use; P3 adds a dedicated `NOLOGIN`/`NOINHERIT`/`NOBYPASSRLS` worker for source-grant revocation only (`20261105120000_account_deletion_worker_slice.sql`, `services/evidence-service/evidence_service/purge_worker.py`). Full purge-worker scope remains deferred. |

## Detailed findings

### Endpoints and policies

#### Implemented HTTP DELETE endpoints

| Method + path | Resource | Service authorization | Database authorization | Notes |
|---|---|---|---|---|
| `DELETE /v1/workspaces/{workspace_id}/evidence/{evidence_id}` | One `public.evidence_records` row in the specified workspace | `require_authenticated_request`, active membership resolution, then `context.role == "owner"`; non-members are returned as workspace-not-found and members receive 403 (`services/evidence-service/evidence_service/app.py:878-900`, `app.py:522-536`). | Supabase REST DELETE uses the caller JWT and workspace/id filters (`services/evidence-service/evidence_service/evidence_repository.py:165-177`). Current RLS is `evidence_records_delete_active_owners`, requiring active owner membership (`supabase/migrations/20261101130000_workspace_evidence_relationships.sql:53-66`). | Enforcement is both service-layer and RLS. The earlier active-member policy was dropped in the same migration (`...sql:7-11`); current behavior is owner-only. |
| `DELETE /v1/workspaces/{workspace_id}/relationships/{relationship_id}` | One `public.evidence_relationships` row in the specified workspace | Authenticated active workspace owner only; `app.py:1000-1023` rejects non-owners before repository access. | Supabase REST DELETE uses caller JWT and workspace/id filters (`services/evidence-service/evidence_service/relationship_repository.py:152-172`). Current RLS is `evidence_relationships_delete_active_owners` (`supabase/migrations/20261101130000_workspace_evidence_relationships.sql:166-179`). | Both layers agree: active owner, not merely an active member. |

The Next.js BFF exposes the corresponding same-origin routes:

| Method + path | Behavior | Evidence |
|---|---|---|
| `DELETE /api/evidence/workspaces/{workspaceId}/evidence/{evidenceId}` | Validates UUIDs and same-origin, verifies the server session, forwards the user access token to the service, and returns 204 only for an upstream 204. | `apps/web/app/api/evidence/workspaces/[workspaceId]/evidence/[evidenceId]/route.ts:29-39`; `apps/web/lib/evidence-api.ts:338-420`. |
| `DELETE /api/evidence/workspaces/{workspaceId}/relationships/{relationshipId}` | Same BFF controls and forwarding behavior for relationships. | `apps/web/app/api/evidence/workspaces/[workspaceId]/relationships/[relationshipId]/route.ts:29-40`; `apps/web/lib/evidence-api.ts:557-627`. |

The BFF does not grant authority. It forwards the verified user's bearer token, so service authorization and Supabase RLS remain authoritative. BFF tests cover same-origin rejection, upstream error sanitization, and DELETE response handling (`apps/web/tests/workspace-integration-api.test.ts:180-199`).

#### Direct authenticated Supabase DELETE capabilities

These are database/API capabilities rather than application routes:

- `DELETE /rest/v1/evidence_records?workspace_id=eq.{workspace_id}&id=eq.{evidence_id}`: direct PostgREST row deletion, subject to authenticated grants and owner RLS. The service repository uses this same operation shape (`services/evidence-service/evidence_service/evidence_repository.py:165-177`).
- `DELETE /rest/v1/evidence_relationships?workspace_id=eq.{workspace_id}&id=eq.{relationship_id}`: direct PostgREST relationship deletion, subject to authenticated grants and owner RLS (`services/evidence-service/evidence_service/relationship_repository.py:152-172`).
- `DELETE /rest/v1/source_grants?...`: direct PostgREST source-grant revocation, subject to active-owner RLS. The integration fixture uses this direct route for revocation (`tests/integration/conftest.py:578-582`). The exact supported filter set is not wrapped by an application route.
- `DELETE /rest/v1/source_snapshots?...`: direct PostgREST catalog deletion attempt, denied by missing authenticated DELETE privilege/policy and the database trigger described below.
- `DELETE /storage/v1/object/source-snapshots/{object_key}`: direct Supabase Storage object deletion attempt, denied for the source-snapshots bucket by the absence of a DELETE policy. The integration fixture constructs this path (`tests/integration/conftest.py:618-621`).

- `public.evidence_records`: authenticated has DELETE table privilege, but current RLS permits only active owners (`20261018150000_workspace_scoped_evidence_records.sql:23-32`; replacement owner policy in `20261101130000_workspace_evidence_relationships.sql:53-66`).
- `public.evidence_relationships`: authenticated has DELETE privilege and RLS permits only active owners (`20261101130000_workspace_evidence_relationships.sql:101-108`, `166-179`).
- `public.source_grants`: authenticated has DELETE privilege; `source_grants_delete_active_owners` permits an active workspace owner to revoke a grant (`20261022100000_source_snapshot_catalog.sql:211-214`, `312-325`). There is no Evidence Service or BFF grant-management DELETE endpoint; this capability is direct Data API access if exposed by the deployed Supabase schema.
- `public.source_snapshots`: authenticated has no DELETE grant or DELETE policy. A trigger also rejects DELETE even for a table owner (`20261022100000_source_snapshot_catalog.sql:200-209`, `268-269`, `378-400`).
- `storage.objects` in bucket `source-snapshots`: there are INSERT and SELECT policies only. No UPDATE, DELETE, or ALL policy exists (`20261022110000_source_snapshot_storage_policies.sql:1-12`); integration tests exercise owner and grantee DELETE denial (`tests/integration/test_local_storage_snapshot_policies.py:123-148`, `260-285`).

#### Workspace and membership deletion

Authenticated clients cannot delete workspace or membership rows. `workspaces_client_delete_denied` and `workspace_memberships_client_delete_denied` are restrictive `using (false)` policies (`supabase/migrations/20261016100000_saas_identity_workspace_boundary.sql:79-94`, `120-125`). The tables grant authenticated SELECT (and workspace-owner name UPDATE), but no client DELETE grant (`...sql:30-38`). There is no service/BFF DELETE endpoint for either resource. Database FKs nevertheless define cleanup semantics if a trusted non-client operation deletes a parent: memberships cascade from workspaces and from `auth.users` (`...sql:13-20`).

### FK / cascade behavior

- Deleting an evidence record cascades to every relationship where it is endpoint A or B. Both composite FKs, `evidence_relationships_evidence_a_fkey` and `evidence_relationships_evidence_b_fkey`, use `ON DELETE CASCADE` (`supabase/migrations/20261101130000_workspace_evidence_relationships.sql:90-95`). This is database-level cleanup, not an application-side follow-up DELETE; it does not orphan or restrict the evidence delete.
- `evidence_records.workspace_id` uses `ON DELETE CASCADE` from workspaces (`supabase/migrations/20261018150000_workspace_scoped_evidence_records.sql:4-7`), and `evidence_relationships.workspace_id` also cascades from workspaces (`20261101130000_workspace_evidence_relationships.sql:68-70`). Client workspace DELETE is denied, so these cascades are not reachable through the client policy.
- `source_snapshots.workspace_id` uses `ON DELETE RESTRICT` (`supabase/migrations/20261022100000_source_snapshot_catalog.sql:142-150`). This blocks trusted workspace deletion while catalog rows exist; it does not delete Storage objects.
- `source_grants` references `(workspace_id, principal_id)` membership with `ON DELETE CASCADE` (`20261022100000_source_snapshot_catalog.sql:176-190`). Revoking a grant is a row delete; it does not delete a snapshot row or object.
- `account_lifecycle.subject_id` references `auth.users` with `ON DELETE RESTRICT` (`supabase/migrations/20261102120000_account_lifecycle.sql:43-45`). The lifecycle migration's preflight explicitly says deletion-request and journal tables are absent (`...sql:8-20`).

### Source snapshots and Storage: staged versus published

An authenticated client can insert only a staged catalog row as the active owner, and the row is visible to its creator while staged (`supabase/migrations/20261022100000_source_snapshot_catalog.sql:203-249`, `251-266`). It cannot delete either staged or published `source_snapshots` rows. The catalog immutability trigger permits only the verifier-controlled staged-to-published transition and the delete trigger rejects every catalog DELETE (`...sql:327-400`). Therefore:

- **Staged catalog row:** client DELETE is denied. A same-owner authenticated client may upload the corresponding staged Storage object, but Storage DELETE is denied and there is no automatic cleanup of unpublished objects (`supabase/migrations/20261022110000_source_snapshot_storage_policies.sql:97-115`; `docs/phase3-source-read-contracts.md:120-121`).
- **Published catalog row:** client DELETE and UPDATE are denied. Published metadata requires verification and is immutable after publication (`20261022100000_source_snapshot_catalog.sql:161-170`, `350-374`). Published bytes are readable only to an active member with an explicit source grant, but no authenticated client may delete the object (`20261022110000_source_snapshot_catalog.sql:220-249`; `20261022110000_source_snapshot_storage_policies.sql:117-138`).
- **Verifier/elevated roles:** the scoped verifier publication path has no catalog or Storage delete authority; its role-safety tests explicitly assert no insert/delete on `source_snapshots` and no Storage delete (`supabase/tests/snapshot_verifier_identity.test.sql:42-61`).

### Audit, lifecycle, and deletion guards

- **Dedicated deletion audit:** **PARTIAL.** Existing evidence and relationship DELETE triggers now write `public.evidence_deletion_audit` in the same transaction (`supabase/migrations/20261105100000_evidence_deletion_audit.sql`); authenticated clients have no audit DML privileges and mutation triggers reject UPDATE/DELETE. This is a database-local audit for the two existing paths; an independent external journal is deferred optional hardening (ADR 0009), not a Phase 8 exit gate. The request/approval model, deletion worker, and verification API described as absent in `docs/data-control-phase8-design.md:28-39` remain separate scope items. `private.snapshot_verifier_allowlist_audit` remains separate and records only verifier allowlist changes (`20261023150000_snapshot_verifier_admin_audit_guards.sql:1057-1116`).
- **Account lifecycle evidence:** `public.account_lifecycle` stores current state, request ID, transition ID, actor class, reason, and timestamps, but it is not a deletion request table or independent append-only log. Its current migration installs trusted provision/transition roles and no client DML (`20261102120000_account_lifecycle.sql:43-120`; `supabase/tests/account_lifecycle.test.sql:78-118`).
- **Deletion-in-progress / cancel guard:** **PARTIAL.** The service performs a fresh external Recovery Fence check and account-lifecycle check before each protected repository/API operation (`services/evidence-service/evidence_service/app.py:243-289`). `account_lifecycle` includes `revocation_pending`, `revoked`, `purge_pending`, `purge_failed`, and `purged`; every state other than `available` returns a fixed 403 before workspace/repository/catalog/Storage work (`20261102120000_account_lifecycle.sql:43-145`, `tests/test_evidence_service_account_lifecycle.py:265-377`). This protects the affected account across all current `/v1` data routes, but there is no installed workspace-specific lifecycle table or workspace revocation transition. Cancellation after revocation remains unsupported as specified (`docs/data-control-phase8-design.md:194-215`).
- **Elevated worker/service role:** No user deletion path uses `service_role`. P3 adds a dedicated non-login, non-inheriting, non-BYPASSRLS worker for the bounded source-grant slice only; it has no Storage, snapshot, or evidence SELECT capability (`supabase/migrations/20261105120000_account_deletion_worker_slice.sql`; `docs/deletion-full-purge-p3-worker-slice.md`). The full worker privilege contract and execution coverage remain deferred (`docs/data-control-phase8-design.md:47-50`, `425-432`).

### Restore consistency evidence

Existing material is documentation and local tests, not a production restore result:

- `docs/adr/0007-independent-recovery-ledger.md:105-125` states that local replay tests cover stale-DO reconciliation and failure cases, while the independent provider, C0 deletion journal, and real Cloudflare PITR drill remain unimplemented.
- `docs/adr/0008-unified-independent-journal.md:108-141` describes a local mixed deletion/Recovery event-contract prototype and explicitly says it does not prove durable acknowledgements, concurrent writer serialization, provider restore independence, or the C0 journal.
- `docs/phase8-recovery-fence-journal-closure.md:21-27,64-82` records the local contract and lists the real restore and C0-journal integration as deferred operational gates. ADR 0009 defers the external journal as optional hardening; it is not a Phase 8 exit gate.
- `docs/data-control-phase8-design.md:309-320` specifies the proposed post-restore sequence: close access outside the restored database, load authoritative deletion history, replay denial and effects, verify absence/retention, then reopen only a verified epoch. It is a design specification, not current runtime behavior; the external-journal dependency is deferred optional hardening under ADR 0009.
- `docs/RLS_INTEGRATION_TEST_REPORT.md:77-94` records a historical isolated pgTAP result (366/366) and states that it is local test evidence, not hosted production evidence. No production restore was run for this inventory.

### Gaps versus a strict deletion-phase closure

- Only evidence records, explicit relationships, and source-grant revocations have current row-delete behavior; P1 adds an account deletion request/status workflow but no purge execution for source, version, workspace, account data, or Auth identity.
- Evidence and relationship deletes now have a durable database-local success event. P1 adds an account-scoped request ID and immutable account-root target manifest, but there is still no independent completion proof or external append-only journal. HTTP 204 remains only the current repository operation's response path.
- Cascading relationship cleanup is automatic and atomic at the database level, but there is no deletion event recording which relationships were removed.
- Source catalog rows and snapshot objects are intentionally append-only/undeletable for clients. Staged orphan cleanup and published physical purge are not implemented.
- Workspace deletion is blocked by both client policy and `source_snapshots` `ON DELETE RESTRICT`; a trusted cascade would remove some child rows but cannot remove snapshot objects and is not a supported workflow.
- Recovery fencing and the account lifecycle state are now composed as fail-closed request gates in the Evidence Service, but they are not connected to an independent deletion journal or workspace-specific lifecycle state. Account lifecycle remains current-state protection, not a complete deletion request/cancel protocol.
- Existing restore documents specify desired reconciliation, but no current deletion journal, deletion worker, or production restore proof establishes post-delete consistency.

## Explicit out-of-scope notes

This inventory does not propose or assess JEV, connectors, billing, exports, new migrations, new APIs, worker implementation, production restores, or refactors. References to the deletion design are cited only to distinguish documented future behavior from behavior currently present.

## Frozen post-closure backlog

- Obtain human security/data-owner approval and a reviewed production migration/deployment plan.
- Integrate and independently retain the deletion journal/recovery authority with prepared/accepted/completed receipts.
- Define production worker leases, retries, observability, secret custody, and `purge_failed` operations.
- Resolve owner/workspace retention and remaining account target enumeration.
- Define and verify exact staged/published Storage and snapshot handling.
- Perform provider-backed PITR/reconciliation and add terminal `purged` completion proof only after all retention and external-journal gates pass.
