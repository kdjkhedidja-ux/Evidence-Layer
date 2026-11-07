# Evidence Layer — Implementation Audit

Date: 2026-11-07. Scope: current repository, local test suites, and read-only probes of the live deployment. No production change was made for this audit. No authenticated session was available, so authenticated flows are marked accordingly.

## 1. Status matrix

Legend: **Code** = implemented and tested locally; **Prod** = deployed and usable against the live project; **Blocked** = code exists but cannot work in production yet.

| Area | Code | Prod | Notes |
|---|---|---|---|
| Public site (`/`, `/documentation`, `/cost-performance`, `/privacy`, `/terms`) | Yes | Yes | HTTP 200 on the live domain. |
| Auth (signup, login, reset, recovery, error) | Yes | Deployed | Supabase Auth and cookie session flows; not exercised without credentials. |
| Workspace console | Yes | Deployed | Membership, personal-workspace provisioning, evidence editor, source panel. Requires a session. |
| Evidence CRUD | Yes | Deployed | Core table `evidence_records` exists in production. Depends on the Evidence Service being reachable. |
| Relationships CRUD | Yes | Deployed | Core table `evidence_relationships` exists. Owner-only writes enforced by service and RLS. |
| Relationship canvas | Yes | Deployed | Client-side SVG; persisted links only, no inference. |
| Source catalogue + verified content read | Yes | Deployed | `source_snapshots`, `source_grants` exist. Publication requires the separately authorized verifier workflow. |
| ROI / cost calculator | Yes | Deployed | Front-end only; estimates, not invoices; no backend. |
| Evaluation pages and reports | Yes | Yes | Local HTML pages under `/evaluation`; no GitHub CTAs. |
| Account deletion request UI | Yes | **Blocked** | Hidden behind `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` (default off) because the production schema lacks the tables. |
| Account lifecycle gate | Yes | **Blocked** | `account_lifecycle` absent in production (`PGRST205`). Risk of 503 on protected routes if the deployed service already contains the gate. |
| Deletion request / manifest / audit | Yes | **Blocked** | `data_deletion_requests`, `data_deletion_manifest`, `evidence_deletion_audit` absent in production. |
| Purge worker slice + work set | Yes | **Blocked** | `data_deletion_work_set` absent; worker CLI not deployed or run. |
| Local restore reconciliation | Yes | Local only | Tested with disposable databases; no production PITR proof. |
| Snapshot verifier service | Yes | Unverified | Code and tests exist; deployment/credentials not confirmed by this audit. |
| Recovery fence worker | Yes | Unverified | Cloudflare Worker code exists; deployment not confirmed. |
| Backups / PITR | — | **Unverified** | Requires project-management access. The test project previously showed zero backups. |
| Legal pages | Draft | Deployed as draft | Controller identity, retention, processors, effective date remain explicit publication blockers. |

## 2. Operational now

- Authenticated SaaS shell: Supabase Auth, workspace membership, personal workspace, evidence and relationship editing, source read path.
- Read-only security posture for anonymous users: core tables return `42501` (no SELECT), so no row leakage.
- Public documentation, benchmark, cost and legal pages.
- Relationship canvas and ROI calculator (pure client-side).
- Delivery pipeline: Vercel production deploy on merge; CI covers web, service tests, pgTAP, live integration, browser auth.
- Local review controls: 21 migrations, 435 statements, 362 registry-approved, 0 hard-denied; 24 web test files (164 tests), 16 pgTAP files (524 assertions), 67 Python test files.

## 3. Implemented but not operational in production

1. **Account lifecycle and deletion programme.** Migrations, service gate, request/manifest model, append-only audit, purge worker slice, and restore reconciliation exist and pass local suites. The production database has none of their tables.
2. **Delete account UI.** Merged, tested, and intentionally disabled by feature flag.
3. **Purge worker.** `scripts`/service CLI exists; no deployment, credentials, leases, retries, or monitoring.
4. **Snapshot verifier and recovery fence.** Code exists; this audit could not confirm they run in production.

## 4. Not implemented

- Full account erasure and Auth identity deletion.
- Storage object deletion (staged or published) and bucket policy changes.
- Published snapshot physical purge and terminal `purged` state.
- Workspace-specific lifecycle and owner-unanimity deletion flow.
- External independent deletion journal and durable acknowledgement.
- Production PITR/restore drill tied to deletion history.
- Billing, connectors, live JEV provider integration, exports.

## 5. Critical risks

| Risk | Impact | Evidence |
|---|---|---|
| Production schema lags code | Account deletion cannot work; protected routes may fail closed if the service contains the lifecycle gate | `PGRST205` for the five deletion tables; core tables return `401/42501` |
| No verified backup/PITR | Migration rollback and restore consistency are unproven | No recovery point recorded in this audit |
| Authenticated RLS unverified live | Owner/member isolation is only proven locally | Local pgTAP 524 assertions; no live session used |
| Deletion feature exposure | UI is currently hidden; enabling before migration would ship a dead flow | `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` default off |
| Legal pages incomplete | Live service handles account data while the notice is a draft | Publication blockers in `/privacy` |

## 6. Recommended order

1. Run `scripts/production_smoke.py` with a test account to confirm protected routes and the lifecycle gate.
2. Verify a recent backup/PITR point for the production project.
3. Apply the reviewed migrations through the release process; re-probe for `PGRST205`.
4. Run the authenticated deletion flow against staging, then enable the feature flag.
5. Finalise legal facts (controller, retention, processors) before public deletion claims.
6. Then proceed to Storage/snapshot purge, terminal `purged`, worker operations, and provider PITR proof.
