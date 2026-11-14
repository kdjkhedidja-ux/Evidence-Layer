# Evidence Layer Architecture

## Status

This document describes the **approved target architecture**, while marking
which parts are present. The repository contains a Python library, evaluation
scripts, dataset artifacts, a JEV scorer, a local stdio MCP server
(`retrieve_evidence`), and authenticated Next.js Workspace and Evidence
Explorer code backed by Supabase Auth/RLS and the Evidence Service. The root
Explorer reads actual workspace data; it does not fall back to local fixtures.

The currently observed Vercel Production deployment is still on main commit
`0bf0c2b2` and predates the later Phase 8 feature-branch work. No Production
deployment or database migration is made by this branch's local changes. The
Railway Production health endpoint returned HTTP 200, but service version,
identity configuration, and protected routes have not been verified against
the hosted Production environment. The repository service code separately
implements verified identity, workspace membership, evidence CRUD,
relationships, and verified source reads using user-scoped Supabase tokens.

The repository does not contain a production source router, a Decisions API
client, or the SaaS backend described below; it has only a local deterministic
routing library (`src/evidence_layer/routing/`) that is not wired into any
service.

Delivery phasing follows the canonical [Product Roadmap](ROADMAP.md): Phase 5
requires mandatory per-user tenant isolation that protects each user's data;
Phase 6 adds advanced team management, shared workspaces, billing, and broader
collaboration. Phase 6 does not defer Phase 5 isolation.

## Target System Context

```text
Agent / MCP client
       |
       v
Railway: persistent MCP server
  retrieve_evidence tool
       |
       +---- Supabase: Auth, Postgres, pgvector, Edge Functions
       |       documents, chunks, tenant data, usage
       |
        +---- Source routing (planned)
        |       deterministic baseline; Decisions option under evaluation
        |       authorization-filtered source catalog
        |
        +---- Retrieval and evidence scoring
        |       JEV only when policy and transfer authorization permit
        |       whole-request lexical fallback on any JEV failure
        |       caller's model generates final answers
       |
       +---- Vercel: Next.js dashboard
               connections, usage, Comparison Lab
```

Stripe handles plan billing and credit packs through server-side, auditable
billing workflows. It is a billing integration, not the source of truth for
document data or scorer behavior.

## Components and Responsibilities

### Python evidence core (exists)

- Provider-agnostic Pydantic schemas for tasks, candidates, policy, results,
  and evaluation cases.
- Deterministic `LexicalScorer`, candidate ranking, and evaluation metrics.
- `JevEvidenceScorer` behind the provider-selection policy; JEV is primary in
  the approved architecture, with whole-request lexical fallback.
- Current `DocumentationRAGAdapter` is a no-op/greedy baseline; it is not a
  production retrieval service.

### MCP server

- A local stdio MCP server is implemented and exposes `retrieve_evidence`.
- Its standard tool path constructs `LocalCorpusRetriever()` without an
  injected JEV policy and is lexical-only, but the retriever's default source
  boundary denies corpus reads before opening content. The tool therefore does
  not currently return corpus evidence without a future trusted source boundary.
- The FastAPI identity/data service at `services/evidence-service/` exposes
  lifecycle-gated `/v1` account, workspace, evidence, relationship, source, and
  deletion routes backed by Supabase Auth and RLS. Workspace task-context,
  personal API-key retrieval, and hosted Remote MCP share bounded,
  relationship-aware retrieval. Lexical scoring is the default; optional JEV
  scoring requires both per-request consent and the server-side
  `EVIDENCE_JEV_ENABLED` flag, which defaults off. The local stdio MCP policy
  remains unchanged.
- Railway hosts the FastAPI service with workspace task-context, personal
  API-key retrieval, and Remote MCP. The production task-context smoke and the
  API-key/MCP checks are recorded in `docs/operations/`.
- The hosted service authenticates and authorizes the caller, establishes
  tenant context, validates requests, invokes bounded retrieval/scoring, and
  returns sanitized results.
- It keeps secrets server-side and applies lifecycle, membership, RLS, and
  rate-limit gates. Per-task token, cost, latency, and answer-quality telemetry
  is not yet persisted.

### Supabase backend (implemented core; broader schemas planned)

- **Postgres + RLS:** authoritative accounts, personal and organization
  workspaces, memberships, evidence records, relationships, verified source
  catalogue metadata, deletion requests/manifests/audit, and personal API-key
  hashes.
- **Auth:** hosted Supabase Auth is integrated for signup, confirmation,
  recovery, login, and TOTP MFA.
- **Storage:** verified source snapshots and the deletion Storage-cleanup queue.
- **pgvector:** optional embedding/vector search if justified by retrieval
  needs; lexical scoring remains the deterministic whole-request scoring
  fallback when JEV scoring fails.
- **Edge Functions:** not the primary backend boundary; the reviewed FastAPI
  service and SQL functions own the current account/data operations.
- Connector documents/chunks, ingestion state, and usage/billing events remain
  planned schemas.

### Authenticated workspace app (implemented; SaaS expansion planned)

- The Next.js app at `apps/web/` is deployed on Vercel from `main`. It provides
  hosted auth screens, personal and organization workspaces, evidence and
  relationship CRUD, verified source-catalogue reads, personal API-key
  management, account-deletion UI, and public ROI/evaluation pages.
- It exposes a narrowed account/personal export path with mediated download
  grants. Hosted retrieval/scoring, source ingestion/publish, shared/group
  export, general download/CDN delivery, usage/plan billing, and the Comparison
  Lab remain absent.
- The browser must not receive provider keys, database service-role secrets,
  or unrestricted billing credentials.

### Connector SDK (read-only slice implemented)

- `src/evidence_layer/connectors/` defines a normalized read-only connector
  contract and adapters for Google Drive, GitHub, Dropbox, Confluence, and
  Notion.
- Adapters accept an already-authorized access token and return normalized
  source items/documents with provider identity, revision, content type, and
  SHA-256.
- Google Drive has a first vertical slice: BFF OAuth connect/callback,
  service-side AES-GCM token vault, manual bounded sync into connector
  documents, and a minimal account UI. Background scheduling, ACL mapping,
  source-snapshot verification, deletion propagation, and production rollout
  remain planned.

### JEV / TypeSafe evidence scoring

- JEV is the intended primary evidence-scoring and ranking provider only when
  explicitly enabled and permitted by policy. Before sending query or candidate
  text to JEV, the trusted server must separately authorize that provider
  transfer and minimize the data sent. Source access alone does not authorize
  this transfer; if authorization is absent or unclear, do not send the data.
- Any JEV failure—including provider errors, timeouts, missing, malformed,
  invalid, or incomplete scores, or any result that fails validation or cannot
  be used safely—requires recomputing the entire candidate set lexically. Never
  mix JEV and lexical scores or rankings.
- The target architecture calls TypeSafe only from a server-side boundary when
  JEV is enabled and permitted by plan and request policy, and separate transfer
  authorization is present. In the current implementation, live JEV transfer
  is hard-denied on supported package paths, including injected dependencies.
- Requested model defaults to `jev-latest`; record the provider-returned model
  when available because the alias may resolve to a changing concrete model.

### Source-routing intelligence (planned; not implemented)

- Deterministic routing is the baseline. The OpenAI Decisions API is an
  experimental option for complex queries and must be evaluated against
  deterministic routing using independently labeled multi-source questions.
  Propose it as a production default only if that evaluation demonstrates
  acceptable routing quality, total cost, and latency.
- Decisions is not required or an approved production default. Any future use
  must select candidates from a trusted catalog already filtered by
  authorization.
- Before transferring query text to Decisions, require separate trusted
  server-side authorization for that provider transfer; permission to access a
  source alone is insufficient. The trusted server must have valid, explicit
  authorization for that specific transfer before sending the query. If
  authorization is absent, unclear, or invalid, do not call Decisions. Minimize
  the query and source metadata sent; never send complete source documents.
  Independently re-enforce source authorization before access.
- Router and Decisions may select only from the server-built authorized source
  catalog. They cannot add sources, grant permissions, bypass tenant
  boundaries, or authorize provider transfers.
- If Decisions is unavailable, times out, or returns invalid output,
  deterministic fallback may proceed only when both the route and eligible
  source set can be established safely and independently authorized. The same
  rule applies when Decisions cannot be called because transfer authorization
  is absent, unclear, or invalid. Otherwise stop rather than guessing,
  broadening access, or bypassing authorization. This routing fallback is
  separate from the JEV-to-lexical evidence-scoring fallback.
- Implementing the source catalog, authorization-filtered router, selective
  Decisions client, output validation, deterministic fallback, and tests is
  future scope. No router or Decisions client currently exists.

### Final answer generation

- The caller's model generates the final answer from the authorized, selected,
  budgeted evidence. This repository does not provide an answer-generation
  provider.

### Stripe billing (planned for Phase 6)

- Manage subscriptions and any future credit packs through verified
  server-side webhook/event processing; commercial terms remain undecided.
- Enforce entitlements in the backend, not the frontend.
- Specify idempotency, refunds, charge disputes, credits, plan periods, and
  usage reconciliation before implementation.

## Request and Data Flow (Target)

1. MCP client calls `retrieve_evidence` with a task and candidate/source context.
2. The future MCP service authenticates the caller and derives tenant
   authorization.
3. A future router filters the trusted source catalog by authorization before
   routing. Permission to access a source does not authorize transferring query
   text to Decisions. Decisions remains an experimental option for complex
   routing; a production-default change requires independent multi-source
   evaluation of quality, total cost, and latency. Any Decisions call during an
   evaluation or future use requires valid, explicit authorization for that
   provider transfer; if authorization is absent, unclear, or invalid, it does
   not call Decisions. It sends only minimum query and source metadata.
   Deterministic routing may proceed without a Decisions call only when both the
   route and eligible source set can be established safely and independently
   authorized. Otherwise the request stops rather than guessing, broadening
   access, or bypassing authorization.
4. Retrieval queries only independently authorized sources selected by the
   routing strategy.
5. Use JEV only when explicitly enabled and permitted by policy and its query
   and candidate-text transfer is separately authorized server-side. If JEV
   fails or returns invalid or incomplete output, recompute every candidate
   score lexically; never mix provider scores. This scoring fallback is
   separate from routing fallback.
6. The service applies the configured evidence/budget policy and returns
   selected/ranked evidence with stable IDs, provenance, and relevant metadata.
7. The caller's model generates the final answer. Usage and latency are
   recorded once per request, with safe provider metadata where available.
8. The dashboard reads authorized account, source, usage, and experiment data.

Account, workspace, evidence, relationship, source-catalogue, and deletion
operations are implemented in the hosted web/service path. A narrowed
account/personal export and mediated download path is deployed with a
`private, no-store` grant flow. Retrieval, scoring, provider-transfer
enforcement, connectors, shared/group export, general download/CDN delivery,
usage metering, and billing remain target capabilities or partial work.

Hosted authentication and tenant-isolation enforcement, database/RLS, the
account/workspace/evidence/source/deletion APIs, organization workspaces,
personal API keys, and the deletion worker are implemented. Missing: a hosted
retrieval/scoring endpoint, hosted provider-transfer authorization, production
source connectors and Decisions integration, source ingestion/publish,
shared/group export, general download/CDN delivery, usage metering/billing,
shared-workspace deletion approvals, and the remaining Phase 8 gates.

## Initial Data Concepts

- **Document:** tenant-owned source item with external identity, metadata,
  synchronization state, and deletion lifecycle.
- **Chunk:** document segment with stable ID, text, provenance, and optional
  embedding/vector representation.
- **Usage event:** request-level record of request count, JEV token counts,
  model identity, latency, and safe outcome category.
- **Experiment/run:** Comparison Lab configuration and aggregate result with
  explicit scope and provenance.

The reviewed migrations and RLS policies for accounts, workspaces, evidence,
relationships, source catalogue, and deletion are implemented. Connector
documents/chunks, ingestion state, usage/billing events, and any final
cross-tenant analytics schema remain logical concepts rather than a frozen
production schema.

## Plans and Limits (Target)

The roadmap's plan and workspace mapping remains a proposal. No pricing,
numeric quota values or periods, credit amounts, or member limits are approved.
Usage metering and safety limits are distinct from payment collection; define
their semantics and backend enforcement before implementing them. Keep plans,
capabilities, entitlements, and observed usage separate rather than treating a
plan label as an authorization decision.

## Security and Reliability Principles

- Tenant authorization must be enforced on every server-side data access.
- Source-access permission does not authorize external transfer of query or
  candidate text. Require separate trusted server-side authorization for each
  Decisions or JEV transfer, minimize input, and fail closed when authorization
  is absent or unclear.
- Keep TypeSafe, Supabase service-role, Google OAuth, and Stripe secrets
  server-side; do not place them in logs or reports.
- Minimize data sent to TypeSafe and exclude source URLs and unrelated content.
- For future Decisions calls, require separate server-side transfer
  authorization; send only the query and minimum source metadata, never complete
  source documents. Filter the trusted catalog by authorization before routing
  and independently re-enforce permissions before source access.
- Sanitize provider errors and avoid request/response body logging.
- Validate connector authorization, deletion, and revocation behavior.
- Use explicit provider errors and lexical fallback rather than returning
  success-shaped defaults.
- Track retries, idempotency, rate limits, and request-level usage explicitly
  before production launch.

## Flexible Configuration and Extensibility Principles

“Flexible by design. Extensible for the future.” is an accepted guiding
principle, not a claim that a centralized configuration service or workspace
system already exists. The target design should:

- Resolve and validate configuration through a clear, centralized boundary;
  version configuration changes and provide safe migrations rather than
  scattering unrelated flags across components.
- Keep subscription plans, product capabilities, entitlements, and measured
  usage limits as separate concepts. A plan label is not authorization, and
  access enforcement belongs on the trusted server.
- Treat a workspace as a logical scope for resources and integrations, not as
  proof of identity or permission. Server-side authorization must enforce
  access within that scope.
- Extend through explicit contracts and incremental, validated additions. Add
  abstractions only when an evidenced requirement needs them.

These are target design constraints. Hosted workspaces and their current RLS
controls are implemented; connector configuration versioning, connector
migration workflows, plan/billing enforcement, and future shared-workspace
administration beyond the current RLS model are not implemented.

## Deployment Boundaries

- Vercel: authenticated Next.js workspace app, public site, and ROI/evaluation
  pages.
- Supabase: hosted Auth, Postgres/RLS, Storage, and reviewed migrations.
- Railway: FastAPI identity/data service and the account-deletion worker; the
  persistent MCP/retrieval service remains planned.
- TypeSafe: external JEV evidence-scoring API.
- OpenAI Decisions API: experimental source-routing option for complex
  queries, evaluated against the deterministic baseline before any proposed
  production-default change; not a scorer or answer generator.

Vercel currently hosts the authenticated workspace app from `main`; Railway
hosts the FastAPI identity/data service and the deletion worker. These
deployments do not mean that hosted retrieval/scoring, connectors, shared/group
export, billing, or full Phase 8 are complete.

## Open Architecture Decisions

- Meilisearch versus Postgres full-text search for lexical retrieval.
- Whether pgvector is needed in the first retrieval release and how it
  interacts with evidence scoring and ranking.
- Selective Decisions API routing criteria, source metadata schema, and
  deterministic routing fallback behavior.
- Decisions API routing quota, cost controls, and operational limits.
- Connector/ingestion schema, usage/billing model, automated audit retention,
  and the remaining shared-workspace/download/export/restore deletion decisions
  (Phase 8 C3/C4/C6/C7).
- Google Drive OAuth scopes and synchronization model.
- Whether the Comparison Lab compares lexical against JEV, or other frozen
  configurations, and what user data it stores.

## Platform Deployment Target

This separates the existing deployments from the target retrieval
architecture. The authenticated workspace app is deployed from `main` on
Vercel; the FastAPI identity/data service and deletion worker are deployed on
Railway. They provide account/workspace/evidence/source/deletion operations but
not hosted retrieval or scoring.

```text
Vercel:
- Authenticated Next.js workspace app at apps/web (deployed)
- public documentation, ROI, and evaluation pages

Supabase:
- Auth, Postgres/RLS, and Storage (deployed)
- migrations are source of truth

Railway:
- FastAPI identity/data service at services/evidence-service (deployed)
- account-deletion worker, one replica (deployed)
- persistent MCP/HTTP retrieval service remains future work
```

Repository scaffolding for these targets lives in `apps/web`, `supabase/`, and
`services/evidence-service/`. See
[ENVIRONMENT_AND_SECRETS.md](ENVIRONMENT_AND_SECRETS.md) for variable handling.
