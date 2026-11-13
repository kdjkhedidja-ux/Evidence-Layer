# Evidence Layer — Implementation Audit (Current)

Date: 2026-11-11

> **2026-11-11 execution update:** The deletion flag is now off unless
> `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED` is exactly `true`. A worker deployment
> template and runbook were added, and migration `20261111120000` now includes
> organization-owned group workspaces/snapshots in account purge. The migration
> was applied to production on 2026-11-11, the Vercel flag was changed from
> `true` to explicit `false`, and the production web build was redeployed. The new
> worker version is deployed with one replica; both personal and organization/
> snapshot smokes reached `purged` and Auth deletion. The flag was re-enabled to
> `true` on 2026-11-11. See
> `docs/operations/DELETION-WORKER-VERIFY-2026-11-11.md`.
Scope: repository code, migrations, tests, configuration, and status documents. The audit itself was read-only; a separate 2026-11-11 execution update (recorded in the addendum and ops runbook) applied the two new migrations, changed the Vercel flag to `false`, then re-enabled it after the dedicated smokes passed, and redeployed the web build. Only synthetic test accounts were purged.

Method note: static inspection was performed across `apps/web`, `services/evidence-service`, `services/recovery-fence`, `services/snapshot-verifier`, `src/evidence_layer`, `supabase`, `tests`, and `docs`. `python scripts/supabase_migration_guard.py validate` and `classify` were executed successfully against the checkout (37 files, 678 statements, 582 registry-approved, 0 hard-denied). The Python and web test suites were **not executed** in this audit environment because `pytest`, `vitest`, and web dependencies are not installed; test counts and results below are static or document-derived and are labelled accordingly. No production endpoint was probed in this audit.

Status legend:

- **PRESENT + FUNCTIONAL** — code exists with tests or clear runtime evidence; “local” means the evidence is repository/CI-level, not a production probe.
- **PRESENT + PARTIAL** — code exists but a test, schema, production, or operational gap remains.
- **MISSING / NEEDS DEV** — no implementation, or an explicit Phase 8 requirement that is not started.
- **Prod** — production state as documented; it is not independently verified here unless stated.

## 1. Executive summary

1. The repository is substantially beyond the status described by the top-level README: it contains a full authenticated Next.js workspace product, a FastAPI identity/data service with 32 protected `/v1` routes, Supabase RLS migrations, organization workspaces, personal API keys, and a bounded deletion program.
2. Local evidence is strong for auth, workspace membership, evidence/relationship CRUD, source catalogue reads, lifecycle gating, deletion request/accept/status, personal-data deletion, and the purge worker sequence. The migration guard passes locally and is included in CI.
3. Production evidence is mixed and mostly document-derived. The 2026-11-07 release applied the first deletion batch; the 2026-11-09 organization closure claims 35 migrations applied and live deletion smokes reaching `purged` plus Auth 404. The current checkout had 37 migrations at audit time; the 2026-11-11 execution update applied the 38th org-workspace purge migration and the 39th idempotent re-prepare migration (verified, pending=0, empty schema diff). The two personal-API-key migrations still have no separate closure record.
4. At audit time the deletion UI was enabled by default in code (`flag !== "false"`), while `docs/ENVIRONMENT_AND_SECRETS.md` still said it was hidden/disabled. The 2026-11-11 execution update changed the code to off-unless-`true`, updated ENVIRONMENT, and added a closure addendum; the production value was confirmed `true`, changed to explicit `false` during verification, then re-enabled to `true` after both dedicated smokes passed; the production web build was redeployed.
5. A continuous account-deletion worker exists, but there is no repository evidence that it is deployed, scheduled, monitored, or given leases/retry handling. An accepted account is denied access immediately; if no worker runs, that account can remain blocked indefinitely.
6. A deletion edge case existed for organization owners: account purge preparation selected personal and sole-owner workspaces only, while group workspaces have `admin` memberships and snapshots use `ON DELETE RESTRICT`. The 2026-11-11 execution update fixed this with migrations 38/39, deployed the new worker, and passed a snapshot-populated organization smoke reaching `purged` with Auth 404.
7. Full Phase 8 remains open. Narrowed C6 export and C4 mediated export delivery/revocation are implemented, deployed, and production-smoke-tested. Shared-workspace owner unanimity (C3), isolated restore environment (C1), full C5 provider absence proof, and provider-backed restore reconciliation (C7) remain deferred. C8 is closed for the narrowed local scope only. ADR 0009 validly removes the external WORM journal from Phase 8 scope.
8. Scoring is present as a library and as historical evaluation artifacts: lexical is the safe default, JEV is optional/hard-denied in the package policy, and the hosted service exposes CRUD/source reads but **no retrieval or scoring endpoint**. The product’s scoring capability is therefore not wired into the production SaaS path.
9. Sources support catalogue listing, exact-version provenance, and verified byte reads. There is no user-facing source ingestion/publish workflow and no confirmed production verifier deployment.
10. Operations have CI, a reviewed migration manifest, a manual production apply guard, and a logical backup/restore drill. Production still has PITR disabled, zero listed managed backups, and the `Production` environment had no protection rules in the last recorded review.
11. Security basics are good: anonymous table access is denied, secrets are not tracked, user request paths reject `service_role`, and `service_role` appears only in dedicated Storage/Auth worker paths.
12. Documentation drift is now a first-class product risk: README, ARCHITECTURE, ROADMAP, the public documentation page, and parts of ENVIRONMENT describe a synthetic/health-only prototype that no longer matches the code or the 2026-11-09 production closure.
13. Overall verdict: **PRESENT + PARTIAL for production readiness**. The core SaaS shell is real and locally testable; the deletion program is materially implemented but operationally and evidentially incomplete; Phase 8 is **not closed**.

## 2. Matrix by domain

| Domain | Status | Evidence (paths) | Gap |
|---|---|---|---|
| Public site, documentation, evaluation, and performance-planning pages | **PRESENT + FUNCTIONAL (local; public pages live per docs)** | `apps/web/app/(public)/**`; `apps/web/app/page.tsx`; `apps/web/app/(public)/evaluation/page.tsx`; `apps/web/app/(public)/cost-performance/page.tsx`; `apps/web/public/evaluation/*.html`; smoke evidence in `docs/PRODUCTION-SMOKE-2026-11-07.md` | Public documentation page still describes a fictional explorer and unhosted service (`apps/web/app/(public)/documentation/page.tsx:49`), contradicting the live workspace product. |
| Legal pages | **PRESENT + PARTIAL** | `apps/web/app/(public)/privacy/page.tsx:11-17`; `apps/web/app/(public)/terms/page.tsx:7-13` | Draft blockers remain: controller identity, effective date, retention, processors, governing law, commercial terms. Production handles account data while the notice is a draft. |
| Auth UI: signup, login, confirmation, recovery, error | **PRESENT + FUNCTIONAL (local)** | `apps/web/components/auth-form.tsx`; `apps/web/app/auth/callback/route.ts`; `apps/web/lib/supabase/recovery*.ts`; `apps/web/tests/auth-*.test.*`, `recovery*.test.*` | Production flows are only partially evidenced; no independent end-to-end email/SMTP confirmation in this audit. |
| MFA and account profile | **PRESENT + PARTIAL** | `apps/web/app/mfa/*`; `apps/web/components/auth-account.tsx`; `docs/AUTH-HARDENING-2026-11-10.md` | Mandatory enrollment and recovery codes are not offered; no per-device session inventory; access tokens remain valid until `exp` after refresh-token revocation. |
| Personal and organization workspaces | **PRESENT + FUNCTIONAL (local; production claimed)** | `services/evidence-service/evidence_service/organization_repository.py`; `20261109120000_account_type_and_organization.sql`; `20261109130000_organizations_and_group_workspaces.sql`; `docs/ORGANIZATION-WORKSPACES-CLOSURE-2026-11-09.md` | One organization per owner; members must already exist; no email invitations; group UI only for organization accounts; no multi-organization membership. |
| Evidence CRUD | **PRESENT + FUNCTIONAL (local; core prod table exists)** | `services/evidence-service/evidence_service/app.py:1496-1645`; `evidence_repository.py`; `20261101130000_workspace_evidence_relationships.sql`; `tests/test_evidence_service_evidence.py` | Authenticated production smoke only exercised reads; no recorded production create/update/delete smoke. |
| Relationships and canvas | **PRESENT + FUNCTIONAL (local)** | `app.py:1647-1767`; `relationship_repository.py`; `apps/web/app/relationships/*`; `tests/test_evidence_service_relationships.py`, `apps/web/tests/relationship-canvas.test.tsx` | Production mutation behavior is not independently exercised; owner/admin write policy is intentionally coarse. |
| Sources: catalogue, verified content, provenance | **PRESENT + PARTIAL** | `app.py:1456-1494,1769+`; `secure_snapshot_read.py`; `supabase_source_catalog.py`; `tests/integration/test_local_verified_retrieval.py`; `docs/phase3-source-read-contracts.md` | No hosted upload/publish path; snapshot verifier deployment/credentials unconfirmed; source index is list-only with no detail route; no staged-object cleanup UI. |
| Cost & Performance planner | **PRESENT + FUNCTIONAL (front-end only)** | `apps/web/app/(public)/cost-performance/*`; `apps/web/lib/roi-calculator.ts`; `apps/web/lib/performance-calculator.ts`; `apps/web/tests/roi-calculator*.test.*`, `apps/web/tests/performance-calculator.test.ts` | Planning estimates for tokens, API calls, cost, and p50/p95 latency; no backend, no per-workspace metering, invoice, or live pricing API. |
| Evaluation and benchmark pages | **PRESENT + FUNCTIONAL (historical artifacts)** | `apps/web/app/(public)/evaluation/page.tsx`; `apps/web/public/evaluation/*.html`; `reports/phase6_*`, `reports/phase7_*` | Static historical reports; no workspace evaluation, no live comparison lab, no re-run from the product. |
| Evidence Service auth and lifecycle gate | **PRESENT + FUNCTIONAL (local)** | `app.py:405-434,645-662`; `account_lifecycle.py`; `tests/test_evidence_service_account_lifecycle.py`; route-inventory test asserts 32 protected `/v1` routes and only `/healthz` public | Every protected route also requires a configured external Recovery Fence; deployment configuration and production behavior are not proven here. |
| Deletion request / accept / status | **PRESENT + FUNCTIONAL (local; prod tables applied)** | `app.py:849-919`; `deletion_repository.py`; `20261105110000_deletion_request_manifest.sql`; `20261108170000_accept_v3_and_lifecycle_cascade.sql`; `20261108180000_local_manifest_ack.sql` | Account scope only; approval is a single-user checkbox, not shared-workspace owner unanimity; no cancellation after acceptance; `approval_pending` wording implies a review step that does not exist for individuals. |
| Personal workspace/data deletion | **PRESENT + FUNCTIONAL (local)** | `app.py:921-963`; `20261108120000_personal_workspace_deletion.sql`; `apps/web/components/auth-account.tsx:720-780`; `tests/test_evidence_service_account_lifecycle.py:828-872` | Deletes DB rows and queues Storage keys; requires the worker to drain the queue; no production-specific acceptance evidence for snapshot-populated workspaces. |
| Full account purge worker | **PRESENT + PARTIAL** | `account_deletion_worker.py`; `deletion_worker.py`; `storage_cleanup_worker.py`; `20261108130000_individual_account_purge_terminal.sql`; `20261108200000_account_purge_audit_actions.sql`; `tests/test_account_deletion_worker.py`, `test_storage_cleanup_worker.py` | Worker sequence and unit tests exist, including Storage/Auth delete and terminal `purged`; no documented scheduler deployment, lease/retry policy, alerting, or `purge_failed` runbook. The Docker worker entry is the continuous wrapper, not a separate scheduled job. |
| Deletion UI and feature flag | **PRESENT + FUNCTIONAL; semantics changed 2026-11-11** | `apps/web/lib/deletion-flag.ts`; `apps/web/components/auth-account.tsx:58-60,720+`; `apps/web/tests/deletion-flag.test.ts`; `docs/operations/DELETION-WORKER-VERIFY-2026-11-11.md` | At audit time the code defaulted ON unless the value was exactly `"false"`; it now defaults OFF and requires exactly `"true"`. Production env was inspected: it was `true`, changed to explicit `false` during verification, then re-enabled to `true` after both smokes passed. ENVIRONMENT and the closure addendum match the current state. |
| Data layer: migrations, RLS, audit tables | **PRESENT + FUNCTIONAL (local; prod partial)** | `supabase/migrations/*.sql`; `supabase/tests/*.sql`; `supabase/migrations.manifest.json`; guard run in this audit | 37 migrations at audit time; 39 applied and verified in production as of 2026-11-11, with pending=0 and an empty remote schema diff. Audit retention (365 days) is not automated. |
| Workers: privileges and service_role | **PRESENT + PARTIAL** | `20261105120000_account_deletion_worker_slice.sql`; `tests/test_purge_worker.py`; `account_deletion_worker.py:60-75`; `storage_cleanup_worker.py` | `account_deletion_worker` is NOLOGIN/NOINHERIT/NOBYPASSRLS; Storage/Auth `service_role` is confined to worker env. Secret custody, network isolation, worker deployment identity, and rotation are not proven. |
| Personal API keys | **PRESENT + FUNCTIONAL (local); production unverified** | `20261110230000_personal_api_keys.sql`; `20261110233000_grant_personal_api_key_subject.sql`; `personal_api_key_repository.py`; `app.py:1029-1183`; `apps/web/app/workspace/personal-api-keys.tsx`; `tests/test_evidence_service_personal_api_keys.py` | No production closure/apply record; `PERSONAL_API_KEY_GATEWAY_SECRET` is absent from both `.env.example` files and from `docs/ENVIRONMENT_AND_SECRETS.md`; read paths return 503 if the service secret is missing. |
| Scoring: lexical default, JEV optional, policy | **PRESENT + FUNCTIONAL (library); not wired to SaaS** | `src/evidence_layer/scoring/*`; `src/evidence_layer/authorization.py`; `tests/test_scoring.py`, `test_provider_policy.py` | `ProviderSelectionPolicy` always reports `jev_selected == False` and recomputes lexically; the hosted Evidence Service has no `/retrieve` or scoring route. Production JEV use is not enabled or authorized. |
| Phase 6 evaluation: matrix, smoke, JEV vs lexical, fake E2E | **PRESENT + FUNCTIONAL (historical/local)** | `docs/phase6-readiness-gate.md`; `docs/phase6-operational-model-matrix-v1.md`; `docs/phase6-jev-vs-lexical-report.md`; `docs/phase6-offline-fake-e2e-pipeline.md`; `reports/phase6_*` | Local pipeline ready; external evaluation not authorized; operational matrix/pricing not fully frozen; exposed test split is not blind; no production policy change. |
| Phase 7 evaluation: E2E RAG, Anthropic, Haiku lexical | **PRESENT + FUNCTIONAL (historical/exploratory)** | `docs/phase7_end_to_end_benchmark.md`; `docs/phase7_anthropic_end_to_end.md`; `docs/phase7_haiku_lexical_only.md`; `reports/phase7_*` | No live JEV calls in Phase 7; JEV cost is an estimate; total system cost partly unknown; 40 developer-labelled cases; not production readiness. |
| Phase 8 / data control | **PRESENT + PARTIAL; full Phase 8 OPEN, narrowed Phase 8 CLOSED** | `docs/PHASE8-CLOSURE-STATUS-2026-11-08.md`; `docs/PHASE8-SCOPE-DECISIONS-2026-11-11.md`; `docs/operations/account-export.md`; current export/deletion code | C6/C4 implemented and deployed for account/personal scope; C8 narrowed acceptance closed with production smoke. C1/C3/full C5/C7 deferred; full Phase 8 not closed. |
| CI, migration guard, release controls | **PRESENT + FUNCTIONAL (local)** | `.github/workflows/python-service.yml`; `.github/workflows/supabase-migrations.yml`; `scripts/supabase_migration_guard.py`; `tests/test_supabase_migration_guard.py` | Production apply is manual and guarded, but the `Production` environment had no protection rules at the last recorded review; the 2026-11-11 plan/apply/verify record covers migrations 38 and 39; the Production environment still has no protection rules. |
| Security: anonymous deny, secrets, service_role on user paths | **PRESENT + FUNCTIONAL (documented production probes)** | `docs/operations/production-verification-2026-11-07.md`; `docs/PRODUCTION-SMOKE-2026-11-07.md`; `services/evidence-service/evidence_service/evidence_repository.py:75`; `apps/web/tests/config-and-secrets.test.ts` | Production probes are dated 2026-11-07 and were not repeated here; Vercel env absence of `service_role`, recovery-fence env, and secret rotation still need operator confirmation. |
| Documentation consistency | **PRESENT + FUNCTIONAL (aligned 2026-11-11)** | `README.md`; `docs/ARCHITECTURE.md`; `docs/ROADMAP.md`; `apps/web/app/(public)/documentation/page.tsx`; `docs/ENVIRONMENT_AND_SECRETS.md`; `docs/operations/production-verification-current.md` | Current docs now describe the authenticated workspace product and the remaining retrieval/scoring/connector/export gaps. Historical closure documents remain point-in-time records and are not rewritten. |

### Phase 8 gate snapshot (updated against current code)

| Gate | Status | Current reading |
|---|---|---|
| C0 — logical contract | **PASS (design)** | Unchanged; ADR 0009 removes the external journal, not the logical contract. |
| C0 — external immutable journal | **OUT OF SCOPE / DEFERRED** | ADR 0009. No WORM claim. |
| C1 — isolated environment + restore capability | **PARTIAL** | Staging readiness/preflight docs exist; no full synthetic-environment acceptance record. |
| C2 — lifecycle + recovery checks | **PASS (local)** | Every protected `/v1` route is gated; Recovery Fence fails closed; 32-route inventory test. |
| C3 — approvals + atomic acceptance | **PARTIAL** | Account-scoped request/accept is atomic; shared-workspace owner unanimity is absent. |
| C4 — download links, caching, revocation | **IMPLEMENTED (production)** | Mediated export grants, 24h expiry, single-use, `private, no-store`, revocation and cleanup; production smoke passed. |
| C5 — fixed-scope deletion | **PARTIAL, improved** | Code now includes Storage queue drain, Auth identity deletion, and terminal `purged`; absence proof and provider-level acceptance remain open. |
| C6 — export | **IMPLEMENTED (production, account/personal only)** | Versioned manifest/checksums, private RLS artifact, size/row caps, expiry/cleanup; production smoke passed; shared/group export deferred. |
| C7 — restore reconciliation | **PARTIAL** | Local reconciliation contract exists; no provider-backed full data-plane restore or deletion watermark. |
| C8 — local + operational acceptance | **CLOSED (narrowed scope)** | 21 pgTAP files/589 tests, 196 web tests, export/worker tests, deletion smoke, and production export smoke passed. |

## 3. Functional map

### What a user or operator can do in the repository today

- Browse the public site, documentation, privacy/terms drafts, cost/performance page, ROI calculator, and static evaluation reports.
- Sign up, confirm email (Supabase Auth), log in, recover/reset password, enroll/challenge TOTP MFA, view/update account profile, sign out, and sign out everywhere.
- Open `/workspace`, provision a personal workspace, list memberships, create/edit/delete evidence records, link evidence to a published source version, browse the source index, read verified source bytes, and manage explicit relationships.
- Upgrade to an organization account, create group workspaces, add an existing user by email, promote/demote/remove organization members, and delete a group workspace.
- Create/list/revoke workspace-scoped read-only personal API keys and use them against evidence/relationship read endpoints.
- Request account deletion, accept it, observe status, and (with the worker) reach `purged`; delete personal-workspace data or the personal workspace itself.
- Run local CI-equivalent tests, the migration guard, and the purge/reconciliation tests.
- Run benchmark/evaluation scripts and inspect frozen reports.

### What is blocked, missing, or only locally proven in production

- **Production worker operation:** no repository evidence of a scheduled account-deletion worker, leases, retries, alerting, or `purge_failed` handling. The UI can accept a request and immediately deny access before any worker is guaranteed to run.
- **Latest schema:** the personal API key migrations and their production application/verification are not documented. Production and the checkout both have 39 applied as of 2026-11-11.
- **Current production deploy/flag:** the Vercel flag is explicit `true`, the web build was redeployed, and the new worker deployment is recorded; the Evidence Service image SHA is still not recorded.
- **Export:** a narrowed account/personal export is deployed and production-smoke-tested; shared/group export is unavailable.
- **Download-link control:** mediated export grants, expiry, no-store, revocation, and cleanup are deployed; production smoke passed.
- **Shared-workspace deletion:** no owner-unanimity workflow; deletion is account-scope only, and group-workspace deletion is admin-initiated.
- **Source ingestion/publish:** no hosted upload/publish UI; the trusted snapshot verifier exists as code but its production deployment/credentials are unconfirmed.
- **Scoring in the product:** no hosted retrieval/scoring endpoint; JEV is not available through the service, and the package policy hard-denies live transfer.
- **Billing, metering, invitations, enterprise SSO, multi-organization membership:** not implemented.
- **Cancellation after acceptance:** explicitly unsupported.
- **Provider PITR / full data-plane restore:** not proven; the recorded backup is a logical `public` + `private` dump excluding `auth`, `storage`, and extensions.

## 4. Needs development (ordered)

### MUST

1. **Make status and flag documentation true.** Update `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/ENVIRONMENT_AND_SECRETS.md`, and the public documentation page to describe the current authenticated product; document the deletion flag’s actual default and the production value. This is the smallest change with the highest operational impact.
2. **Publish a current production verification record.** Record the deployed service SHA, applied migration count (including `20261110230000` and `20261110233000`), deletion flag value, worker deployment/heartbeat, and an authenticated read/write/delete smoke. Do not infer production PASS from local tests or from the 2026-11-09 closure alone.
3. **Operationalize the account-deletion worker.** The new continuous worker version is deployed with one replica and the runbook exists. Remaining: add a lease/claim function, define retry/backoff, alert on `purge_pending` age and `purge_failed`, and rotate the privileged credentials.
4. **Organization-owner deletion edge — completed.** The pgTAP coverage, v3 predicate, idempotent re-prepare, and deployed-worker organization smoke all pass. Keep the regression tests in CI.
5. **Remaining Phase 8 product gates — decisions recorded.** C4/C6 are implemented locally; C3, full C5, C7, and C1 are deferred with residual risk. Production export deployment and a production export smoke remain.
6. **Automate audit retention and deletion absence proof.** The 365-day audit-retention policy is documented but no job enforces it. C5 also needs an explicit absence check that covers database rows, Storage objects, and retained metadata before claiming completion.

### SHOULD

7. **Document and verify personal API keys in production.** Add `PERSONAL_API_KEY_GATEWAY_SECRET` to the service environment documentation and release checklist; add an apply/verification record and a production smoke for create/list/read/revoke.
8. **Define source ingestion/publish ownership.** Either expose a supported verifier/publish workflow or state operationally that sources are seeded by operators. The current UI can read sources but cannot create/publish them.
9. **Add email invitations and richer organization administration.** Current “add member” requires an existing account by email; invitations, role history, and multi-organization membership are absent.
10. **Decide whether scoring belongs in the hosted product.** If yes, add a retrieval/scoring endpoint with the same lifecycle, tenant, source-boundary, and transfer-authorization controls used elsewhere. If no, remove language that implies the service performs evidence selection.

### COULD

11. Billing/metering, per-workspace evaluation, cancellation windows, session inventory, leaked-password protection (plan-gated), SSO, connectors, and enterprise retention controls. These are real product gaps but not blockers for the current deletion/SaaS boundary.

## 5. Suggestions (prioritized, smallest useful increments)

1. **Truth-and-verify increment (days).** One commit updating the stale status docs and flag semantics, plus a generated `docs/operations/production-verification-current.md` populated by the existing `production_smoke.py`/`production_smoke_auth.py` scripts. This removes the largest operator-confusion risk without touching production.
2. **Worker-runbook increment (remaining).** The worker is deployed and smokes pass. Add a heartbeat/age alert for `purge_pending`/`purge_failed` and a lease/claim function; no new architecture is required.
3. **Organization deletion correctness increment — completed.** Migrations 38/39, pgTAP coverage, and a production organization smoke are in place.
4. **Phase 8 decision increment (small).** Record one ADR that either finances/orders C3+C4+C6 or explicitly defers them with residual risk. If implementation is chosen, start with export (data rights) and then mediated download links with revocation; keep the external WORM journal out per ADR 0009.
5. **Production-surface increment (medium).** Verify the snapshot verifier and source-publish operator path in production, then remove the public documentation’s “fictional explorer / unhosted service” claims. If the verifier cannot be operated, keep the source catalogue read-only and say so explicitly.

## 6. Explicit non-goals / out of scope (already decided)

- External immutable deletion journal, WORM/Object Lock, independent forensic store, durable external acknowledgement, and journal-driven restore watermarks — **out of Phase 8 scope / deferred optional hardening** per ADR 0009.
- No AWS/GCS/R2 journal re-architecture in Phase 8.
- No WORM or independent-administration claim for `public.evidence_deletion_audit`; it is database-local and a DBA/service-role compromise can alter it.
- No terminal full-account erasure claim beyond what the current worker/Auth/Storage path actually proves; no claim that backups or already-delivered client bytes are erased.
- No provider PITR/full data-plane restore equivalence.
- No enterprise SSO, billing, email invitation delivery, source connectors, multi-organization membership, or customer Comparison Lab in the recorded organization closure.
- No live JEV transfer in the package policy; evaluation JEV use is separate and explicitly authorized.
- No production authorization is granted by this audit.

## 7. Risks (top 5 residual)

1. **Deletion accepted before a worker is guaranteed to run.** The UI is default-on, acceptance immediately denies access, and worker deployment/monitoring is not evidenced. Result: a user can be permanently locked out of their data with no visible recovery path.
2. **Organization-owner deletion is fixed and smoke-tested**, but depends on the connected Railway source and single-replica worker remaining in sync with the reviewed commit.
3. **Documentation/flag/schema drift misleads operators and users.** README/ARCHITECTURE/public docs describe a synthetic prototype; the flag semantics are now aligned, but the worker image revision and fresh deletion smoke are still missing. Result: wrong enablement decisions and false public claims.
4. **Recovery Fence configuration is a single point of failure.** Every protected `/v1` route requires a configured external fence and fails closed with 503 if it is missing/unreachable. The repository does not document the production fence configuration or a verification receipt.
5. **Restore can resurrect data with no deletion-aware proof.** PITR is disabled, managed backups are zero, the logical dump excludes Auth/Storage, and there is no provider-backed reconciliation. Result: a restore or retained copy can reintroduce data that the product reports as purged. Export grants are now mediated, but already-delivered copies cannot be recalled.

Secondary but material: the `Production` GitHub environment had no protection rules at the last review; `service_role` custody for Storage/Auth workers and the recovery-fence HMAC key are not evidenced in a secret manager; legal notices remain drafts for a live account-handling service.
