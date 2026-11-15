# Evidence Layer Development Plan

**2026-11-02 repository audit and next implementation plan:** see the
[evidence optimization work plan (Arabic)](EVIDENCE_OPTIMIZATION_PLAN_AR.md) and
[ADR 0005: adaptive evidence selection](adr/0005-adaptive-evidence-selection.md).
They inventory the current code, preserve the Phase 7/7B benchmarks, and specify
candidate preparation, token-aware selection, sufficiency, and total-cost gates.
The status and roadmap below were updated on 2026-11-14 from a repository-wide
implementation audit. The adaptive optimization pipeline remains a proposed
implementation, not a shipped feature.

This is the living development plan for the intended Evidence Layer SaaS. It
distinguishes current implementation and deployments from the approved product
direction. Future product capabilities are identified separately from what is
already present.

## Current Status

- **Scientific/evaluation core is implemented:** provider-agnostic schemas,
  deterministic lexical scoring and candidate selection, evaluation metrics
  and harness, dataset validation, and a JEV scorer behind provider-selection
  policy.
- **The committed historical held-out run** measured lexical MAP **0.9735** and
  JEV MAP **0.9924**. The paired MAP difference was
  **+0.0189**, with 95% CI **[−0.0152, +0.0644]**. The predeclared decision was
  **Inconclusive**. See the [Phase 5A plan](phase5a_heldout_test_evaluation_plan.md)
  and [Phase 5B report](../reports/phase5b_heldout_test_single_run.json) —
  historical evaluation-phase numbering, unrelated to the ROADMAP's Phase 5.
- **Approved architecture:** JEV is the intended primary evidence-scoring and
  ranking provider only when explicitly enabled and permitted by policy; retain
  the policy gates and whole-request lexical fallback. The historical
  evaluation decision in ADR 0001 is superseded as
  architecture guidance, while its measurements remain historical.
- **Hosted SaaS foundation is implemented:** Supabase Auth (email confirmation,
  recovery, TOTP MFA), personal and organization workspaces, workspace-scoped
  evidence and relationship CRUD, verified source-catalogue reads, personal API
  keys, account deletion, account/personal export, and RLS-backed FastAPI `/v1`
  routes.
- **Hosted retrieval is implemented and production-verified:** workspace
  task-context, chunked API-key retrieve/get-document, and Remote MCP over
  bounded evidence. JEV is the primary scorer when `EVIDENCE_JEV_ENABLED=true`;
  any JEV failure recomputes the whole candidate set lexically. `use_jev=false`
  forces the lexical path.
- **A content-free retrieval task ledger is deployed:** token/character counts,
  latency, selection counts, scoring provider, and fallback reason are stored;
  task text, evidence text, answers, provider responses, and keys are not.
- **Production connectors are partial:** GitHub App sync and Notion personal
  OAuth/manual sync are deployed; Google Drive remains gated pending OAuth
  verification; Dropbox and Confluence have SDK adapters only.
- **Still missing:** source ingestion/publish UI and background scheduling,
  billing and usage metering, shared/group export, shared-workspace deletion
  approvals, production source routing and the Decisions client, agent
  execution, and per-task cost/quality/citation telemetry. Full Phase 8 is not
  closed.
- **A local stdio MCP server is implemented** and exposes `retrieve_evidence`.
  It remains lexical-only, and its default source-read boundary denies before
  opening corpus content. Final answer generation remains with the caller's
  model.

`LocalCorpusRetriever` is implemented, but its current default source-read
boundary denies before opening corpus content. The retrieval code is designed
to select candidates with lexical overlap, score and rank them, then apply
`budget_words`, which counts `text.split()` words and is not a token count; the
default-deny boundary currently prevents that path from reading corpus records.
The standard MCP stdio path remains lexical-only and likewise cannot read corpus
content without a future trusted source boundary. Live JEV transfer is
hard-denied on supported package paths, including when dependencies are
injected. The hosted FastAPI service is the separate production retrieval path
described above; the local stdio server does not use it.

The test result is one evaluation on this project's held-out dataset. It is not
evidence of production readiness, savings, or generalization to customer
workloads.

The multi-domain product direction below is approved architecture, not a claim
that domain-specific pipelines or evaluations are already implemented.

## Audit-derived development roadmap (2026-11-14)

Source: repository-wide implementation audit of commit `f68b727`, covering
`apps/web`, `services/evidence-service`, `services/recovery-fence`,
`services/snapshot-verifier`, `src/evidence_layer`, `supabase`, `tests`, and
`docs`. Cross-references: [current implementation audit](IMPLEMENTATION-AUDIT-CURRENT.md),
[current production verification](operations/production-verification-current.md),
and [Phase 8 scope decisions](PHASE8-SCOPE-DECISIONS-2026-11-11.md).

Legend: **DONE** = implemented with tests or production evidence; **PARTIAL** =
code exists but a product, schema, or operational gap remains; **MISSING** = no
implementation.

Verified baseline: 47 migrations applied in production (pending=0); 1,378
Python tests passing (15 skipped), 259 web tests passing, and 23 pgTAP files
with 621 tests. Task-context, API-key retrieve, Remote MCP, JEV,
and the content-free task ledger are production-verified.

### Status snapshot

| Area | Status | Current boundary |
|---|---|---|
| Auth, MFA, personal/organization workspaces | **DONE** | No invitations, no multi-organization membership, no session inventory |
| Evidence and relationship CRUD | **DONE** | Owner/admin writes; member reads |
| Verified source reads | **DONE** | Reads only; no hosted ingestion/publish path |
| Hosted retrieve, task-context, Remote MCP | **DONE** | Read-only, workspace-scoped, bounded |
| JEV scoring | **DONE** | Server-gated; whole-set lexical fallback; no provider cost returned |
| Retrieval task ledger | **DONE** | Retrieval metrics only; no agent cost/quality |
| Account deletion and purge worker | **DONE** | Lease/retry active; migration 47 applied; four production smokes passed; single replica until scale-out validation |
| Account/personal export | **DONE** | Shared/group export unavailable |
| Connectors | **PARTIAL** | GitHub and Notion deployed; Google Drive gated; Dropbox/Confluence adapters only |
| Source ingestion/publish | **PARTIAL** | Owner staging API/UI and private object upload are implemented; allowlisted publication/verifier deployment is pending |
| Source routing / Decisions API | **MISSING** | Local deterministic routing library is unwired and denies by default |
| Billing, metering, quotas | **MISSING** | Cost/performance pages are planning estimates only |
| Agent execution and task measurement | **MISSING** | EL returns evidence; it does not run the agent |
| Shared workspace deletion approvals | **MISSING** | Account-scope only; owner unanimity deferred |
| Backup and recovery | **PARTIAL** | Logical B2 dump plus passing restore drill and verifier; PITR disabled; Auth/Storage outside the dump |
| Full Phase 8 (C1/C3/C5-full/C7) | **PARTIAL** | C4/C6/C8 narrowed are delivered; the rest is deferred |

### P0 — Make the product truthful and operable (next)

**P0.1 — Documentation and status truth (updated 2026-11-14)**
- Keep `README.md`, `docs/ARCHITECTURE.md`, `docs/PRD.md`, the public
  documentation page, and this plan aligned with the deployed boundary, and
  check them against `docs/operations/production-verification-current.md` on
  each release. The 2026-11-14 pass removed the stale prototype/health-only
  wording and the old Vercel commit.
- Acceptance: no current status document contradicts
  `docs/operations/production-verification-current.md`; deployed commits, flags,
  and the ledger boundary stay stated consistently. Historical plans and ADR
  decision text are exempt but must be read as point-in-time records.

**P0.2 — Worker operational hardening (CLOSED 2026-11-15)**
- Delivered: migration 47 lease/claim RPC, bounded retry/backoff, lease-owner
  checks, stale-lease and retry-overdue alerts, and the secret custody record.
- Applied and verified: 47 migrations, pending=0. The worker was redeployed
  from `9e4329e` with the lease environment active.
- Production smokes passed:
  - accepted -> purged (attempts=1);
  - purge_failed -> retry -> purged (attempts=2);
  - lease-expiry recovery (attempts=2);
  - healthcheck raised `purge_failed_retry_overdue` and `stale_lease`, then
    returned to `status=ok` after cleanup.
- One replica remains until horizontal scaling is explicitly validated.
- Acceptance met: lease/claim prevents duplicate ownership, expired and
  over-due work alerts, and the runbook was exercised.

**P0.3 — Backup, restore, and deletion reconciliation (CLOSED with explicit limitation, 2026-11-15)**
- Delivered: a logical production dump in private B2 bucket `evidence2`
  (SHA-256 `61df647210bef7d34ff082ffa23c3e892043d15d2949f8a450fd3f2d6b43db6a`),
  a disposable PostgreSQL 17 restore drill, the deletion reconciliation
  verifier (`clean`, 42/42), the recovery attestation, and a passing
  production `plan`.
- Explicit limitation: provider PITR is disabled and no provider-managed backup
  is enabled. The logical dump excludes Auth, Storage, and extensions. A restore
  can reintroduce deleted rows; access stays blocked until reconciliation. This
  limitation is published in the public documentation and legal drafts.
- Acceptance: met via the passing drill plus the explicit limitation.

### P1 — Close the product gaps

**P1.1 — Source ingestion and publish (staging implemented 2026-11-15; publication pending)**
- Delivered: an owner-only `POST /v1/workspaces/{id}/sources` staging endpoint
  plus the workspace form. The service computes SHA-256 and size, creates the
  staged catalog row, and returns the private bucket/object key; the browser
  uploads the bytes to `source-snapshots`. Staged rows are visible to the owner
  as pending verification.
- Remaining: allowlisted snapshot-verifier publication and deployment evidence,
  source read grants, staged-object cleanup, and the end-to-end retrieve
  provenance smoke.
- Acceptance: a workspace can add a new source version end to end, the verifier
  marks it integrity-verified, and retrieve can cite it with exact provenance.

**P1.2 — Phase 8 residual gates**
- Decide and finance C3 (owner unanimity), C5-full (provider absence proof), and
  C7 (restore reconciliation), or keep them explicitly deferred with residual
  risk.
- Acceptance: each gate is either implemented and accepted or named as a public
  limitation; Phase 8 is not described as fully closed until then.

**P1.3 — Connector completion**
- Finish Google Drive OAuth verification, run the Notion live smoke, add
  Dropbox/Confluence OAuth, then background scheduling, ACL mapping,
  incremental sync, and deletion propagation.
- Acceptance: connect, scheduled incremental sync, permission change, and
  disconnect/purge are tested per enabled provider.

**P1.4 — Shared/group export**
- Extend the export producer and mediated download grants to shared/group scope
  after C3 decisions.
- Acceptance: an authorized member can export only what they may read; other
  members' private data is excluded.

**P1.5 — Phase 2 blinded comparison**
- Run the preregistered blinded A/B/Tie/Both-bad review on a sealed,
  previously unseen set.
- Acceptance: at least two independent reviewers per pair, published
  disagreement handling, and no confirmatory claim from the exposed split.

### P2 — Measurement, monetization, and expansion

**P2.1 — Agent task measurement**
- Persist per-task model tokens, cost, latency, answer quality, and citation
  correctness without storing evidence or answer text by default.
- Acceptance: the north-star metric is computed end to end for real tasks.

**P2.2 — Billing and metering**
- Add server-enforced entitlements, usage metering, free-tier limits, Stripe
  subscriptions and lifecycle events; keep metering separate from payment.
- Acceptance: usage and entitlements are reconciled, auditable, and cannot be
  bypassed by the frontend.

**P2.3 — Collaboration administration**
- Add email invitations, multi-organization membership, SSO/SCIM, advanced RBAC,
  and permission inheritance.
- Acceptance: membership changes are auditable and enforced server-side.

**P2.4 — Comparison Lab**
- Add a controlled "Run A/B" workspace experience over frozen configurations.
- Acceptance: experiments record scope, provenance, and explicit limitations.

**P2.5 — Production source routing and Decisions API**
- Wire the deterministic router, add tenant-scoped catalog queries and
  source-read reauthorization, then evaluate the Decisions client against the
  deterministic baseline before proposing a production default.
- Acceptance: routing cannot add sources or grant permissions; absent transfer
  authorization stops safely.

**P2.6 — Adaptive/vector selection**
- Implement the [ADR 0005](adr/0005-adaptive-evidence-selection.md)
  candidate-preparation and token-aware selection pipeline, and add
  embeddings/ANN only if evaluation justifies them.
- Acceptance: quality and total cost are reported against the lexical baseline
  on representative data.

**P2.7 — Domain pipelines**
- Build separate support, coding, compliance, contract, and research profiles
  with their own data, sufficiency policies, and evaluation.
- Acceptance: results are reported per domain; pooled scores are not used to
  hide a regression.

### Sequencing and definition of done

P0 items come before P1, and P1 before P2, unless an operator decision explicitly
reorders them. An item is done only when it has: implementation, automated tests,
production evidence or an explicit local-only boundary, and matching
documentation. Deferred items keep a named residual risk rather than disappearing
from the roadmap.

### Explicitly deferred

- External immutable deletion journal / WORM / independent forensic store
  ([ADR 0009](adr/0009-phase8-external-journal-scope.md)).
- Provider-level absence proof beyond the current worker path.
- Full data-plane restore equivalence.
- Agent execution or answer generation inside Evidence Layer.
- Pro Plus/Enterprise BYOK and private deployment.
- Domain-specific pipelines before their data and evaluation exist.

## Approved Target Architecture

This is the intended architecture. Current deployments are identified below;
the remaining platform components are not infrastructure already present in
the repository:

- **Vercel:** The authenticated Next.js workspace app, public site, and
  ROI/evaluation pages are deployed from `main`. Shared/group export,
  comparison-lab UI, and billing UI remain planned.
- **Supabase:** Auth, Postgres/RLS, and Storage are deployed; pgvector and
  usage/billing schemas remain planned.
- **Railway:** The FastAPI identity/data service, hosted task-context/retrieve/
  Remote MCP, and the account-deletion worker are deployed. Source
  ingestion/publish and background connector scheduling remain planned.
- **JEV:** Intended primary TypeSafe-backed evidence-scoring and ranking
  provider only when explicitly enabled and permitted by policy. Before sending
  query or candidate text, the trusted server must separately authorize that
  provider transfer and minimize the data sent. Source access alone does not
  authorize transfer; if authorization is absent or unclear, do not send the
  data.
- **Lexical:** On any JEV failure—including provider errors, timeouts, missing,
  malformed, invalid, or incomplete scores, or results that fail validation—
  recompute the entire candidate set lexically. Never mix JEV and lexical
  scores or rankings.
- **OpenAI Decisions API:** Experimental option for complex source routing,
  evaluated against deterministic routing using independently labeled
  multi-source questions. Propose a production-default change only if that
  evaluation demonstrates acceptable routing quality, total cost, and latency.
  Deterministic routing is the baseline and may handle simple queries. Filter
  the trusted source catalog before routing and re-enforce source authorization
  before access. Before transferring query text to Decisions, require separate
  trusted server-side authorization and send only minimum query/source metadata,
  never documents. Router and Decisions cannot add sources, grant permissions,
  bypass tenant boundaries, or authorize transfers. Deterministic fallback on
  unavailable, timed-out, or invalid Decisions output may select only safely
  eligible, authorized sources; if none can be established, stop rather than
  widening access.
- **Evidence output:** Returns selected/ranked evidence, provenance, and
  relevant metadata to the caller.
- **Final answers:** Generated by the caller's model, not by this service.

Delivery phasing follows the canonical [ROADMAP.md](ROADMAP.md): Phase 5
requires mandatory per-user tenant isolation and protection of each user's
data. Phase 6 adds advanced team management, workspaces, broader collaboration,
and billing; it does not defer the Phase 5 isolation requirement. Phase 5 usage
metering and free-tier safety limits are separate from payment collection.

The local deterministic routing library in
`src/evidence_layer/routing/` defines a source contract, filters candidate
catalogs through a policy interface (the shipped default denies all), routes
deterministically, validates router output, and provides deterministic fallback.
Fake-based tests cover these local behaviors; they do not establish production
authorization. The library is not wired into a production service.

Production integration still requires trusted context construction from
verified identity and membership, tenant-scoped catalog queries, source-read
reauthorization and revocation checks, transfer authorization, and a timeout
before adding a network-backed router. A Decisions client and selective-call
policy remain unimplemented; evaluate Decisions against deterministic routing
before proposing a production default. Keep this work separate from PR #12's
JEV provider-selection policy.

See [Architecture](ARCHITECTURE.md) for boundaries and the planned request
flow.

## Product Goal

Evidence Layer is one shared evidence-optimization engine, not three separate
products. Its product direction covers three task domains:

- **Document QA and Knowledge Retrieval:** select source passages that support
  each requested fact, preserve provenance, and identify missing or conflicting
  evidence.
- **Coding Agents and Vibecoding:** select repository context such as relevant
  files, APIs, constraints, and tests that an agent needs to make and verify a
  code change.
- **Complex Classification:** select the records, rules, exceptions, and
  boundary evidence needed to assign a label or structured decision reliably.

The shared core prepares and ranks candidates, removes safe redundancy, selects
within a serialized token budget, retains provenance, and coordinates bounded
retrieval expansion. Task profiles supply domain-specific sufficiency policies
and evaluation criteria around that core. JEV is the intended primary candidate
ranker when enabled and separately authorized; lexical ranking remains a whole-
request fallback. The caller's model still generates answers or performs the
downstream task.

Each domain needs its own representative data, definition of task success,
sufficiency policy, quality guardrails, and cost evaluation. Results must be
reported separately by domain; one pooled score can hide a quality or cost
regression in one workload. Shared code does not imply shared sufficiency
thresholds or interchangeable evaluation labels.

This is product direction. The implemented Python evaluation/scoring core,
local MCP server, authenticated evidence service routes, verified source reader,
and connected web screens are described in their current-status and Phase 8
documentation. A domain-aware optimization pipeline, production JEV execution,
hosted retrieval/MCP, customer connectors, and the three domain-specific
evaluation suites remain proposed or planned work. SaaS hosting, durable source
storage, usage controls, billing, and comparison experiences are separate
product-platform work; their target roadmap does not make the optimization
pipeline available today.

## Revised Phases

> **Note:** These implementation phases are historical and are superseded by the
> canonical product phases in [ROADMAP.md](ROADMAP.md) (Phase 0–6: local MCP
> foundation, prove value, Comparison Lab, connector contract, Google Drive,
> SaaS beta, billing and expansion). The names below are retained for history;
> new work should reference the ROADMAP phases.

### Phase 1 — MCP server and real retrieval

- A local stdio MCP server exposing `retrieve_evidence` is already implemented.
- Implement the persistent Railway HTTP MCP server.
- Expose `retrieve_evidence` as the initial tool.
- Add a retrieval pipeline using JEV as the primary evidence scorer, retaining
  whole-request lexical fallback under the provider-selection policy.
- Keep request validation, safe errors, and provider-independent core
  interfaces.

### Phase 2 — Supabase and Google Drive

- Integrate Supabase Postgres, pgvector, Auth, and Edge Functions according to
  the target architecture.
- Store document and chunk records and track usage.
- Add the Google Drive connector as the first real source connector.
- Define ingestion, update, deletion, access-control, and provenance behavior
  before enabling customer data flows.

### Phase 3 — Plans and billing

- Add Free and Pro usage entitlements and enforce them server-side.
- Integrate Stripe subscriptions and billing lifecycle events.
- Define any future credit-pack terms and auditable consumption rules before
  implementation; no amount is approved.
- Do not treat estimates of model cost as customer invoices or margin proof.

### Phase 4 — Comparison Lab and beta

- Add a Comparison Lab with a controlled “Run A/B” experience.
- Show scorer outcomes and operational usage with explicit experimental
  limitations.
- Prepare and run a bounded beta only after privacy, reliability, support, and
  rollback requirements are met. Under the canonical roadmap, Phase 5 beta
  precedes Phase 6 billing.

## MVP Scope

### In scope

- MCP server with a `retrieve_evidence` tool.
- Real retrieval using JEV evidence scoring with whole-request lexical fallback.
- Evaluate the Decisions API as an option for complex source routing against
  the deterministic baseline. A production-default change requires independent
  multi-source evaluation of quality, total cost, and latency; router/client
  integration remains outside the current PR scope.
- Supabase storage for documents, chunks, and usage tracking.
- Google Drive connector.
- Phase 5 usage metering and safety limits; no numeric quota values or periods
  are approved. Usage limits and metering do not imply payment collection.
- Comparison Lab with “Run A/B”.

No numeric plan quota values are approved or implemented. Billing periods,
overage behavior, token accounting, and credit consumption semantics remain
open product decisions.

Paid Pro, Stripe subscription billing, and possible credit packs are Phase 6
targets under the canonical roadmap, outside the Phase 5 beta MVP. Pricing,
amounts, periods, and accounting rules remain open product decisions.

### Out of scope

- BYOK.
- Multi-agent orchestration.
- SSO and advanced RBAC.
- Enterprise SLA.
- Notion connector.
- Public general-purpose REST API.

## Success Metrics

These are target outcomes, not measured results:

- Free-to-paid conversion: **3–5%**.
- LLM token reduction: **40–60%**.
- Gross margin: **70%+ within 6–12 months**.

The token-reduction figure is an unvalidated product-level aspiration. It does
not set an acceptance threshold for every task, and it cannot override each
domain's independent evidence-quality, task-success, and net-cost gates.

Define denominators, cohorts, measurement windows, excluded traffic, and
instrumentation before using these targets to judge a launch.

## Risks and Mitigations

- **JEV API changes:** isolate the provider behind the selection policy, test
  failure handling, and preserve whole-request lexical fallback.
- **Scope creep:** keep the explicit MVP and out-of-scope lists current; require
  a deliberate scope decision for additions.
- **Product scope ambiguity:** treat the intended outcome as an integrated
  SaaS product, not a Python library alone, while keeping the present
  repository state accurately described.
- **Unproven value or economics:** do not infer customer benefit, savings, or
  margin from the existing small benchmark; measure on representative,
  privacy-approved workloads.
- **External data and identity risks:** design tenant isolation, connector
  permissions, deletion, retention, and auditability before storing customer
  content.

## Open Decisions

- Use **Meilisearch** or **Postgres full-text search** for lexical retrieval?
- Define the selective Decisions routing policy, trusted source metadata, and
  deterministic routing fallback separately from JEV scoring policy.
- Define request quota periods, JEV-token metering, separate Decisions-routing
  cost limits, overage behavior, and $5-credit-pack consumption.
- Define the Comparison Lab experiment semantics and safe handling of
  customer-provided content.

Resolve these decisions before the affected phase begins; do not silently
assume an implementation choice.
