# ADR 0003 — Cost- and context-optimization layer: multi-source routing, isolation, and evaluation

- Status: Proposed (design draft for review). On `main` nothing here is
  implemented. A first local, unwired slice is proposed separately in PR #37
  (not merged); see "Implementation status" below for exactly what it covers.
- Scope: design only. This ADR does not authorize code, evaluation data,
  provider calls, production changes, or a change of any default.

## Context

Evidence-Layer sits between retrieval (RAG, structured sources, memory) and
the final LLM. The product goal is a cost- and context-optimization layer, not
a new RAG system. This ADR fixes that goal as the yardstick for architectural
decisions and records the design for multi-source routing, tenant isolation,
and its evaluation. It builds on
[ADR 0002](0002-multi-source-evidence-architecture.md),
[ROADMAP.md](../ROADMAP.md), [PRD.md](../PRD.md), and
[ARCHITECTURE.md](../ARCHITECTURE.md). The Phase 5A/5B ranking protocol,
datasets, and historical report are frozen and are not modified by this ADR;
the historical JEV-versus-lexical result remains `Inconclusive`.

Labels: **[verified]** from code or official documentation read on
2026-10-20; **[proposed]** a proposal; **[decision]** needs an explicit human
decision; **[unverified]** not yet verified; **[after pilot]** cannot be fixed
without development-split data.

## Central objective

> Evidence Layer is a cost- and context-optimization layer between RAG and the final LLM. It minimizes total retrieval and inference cost, unnecessary calls, and token consumption while preserving answer quality, factual accuracy, contextual completeness, provenance, tenant isolation, and authorization. Optimization must never override security, and any claimed savings must be measured against an equivalent baseline.

Operational form [proposed]: minimize the cost of reaching evidence per
successful task, subject to (a) quality not worse than the baseline by a
pre-declared margin and (b) zero accepted security violations.

Cost is the **total cost of a successful answer**: final-LLM tokens, provider
tokens (JEV, Decisions), source/connector retrieval cost, failed or repeated
retrievals, actual retries, and latency. Evidence-Layer's own cost counts, so
net savings are measured, not context size alone. Token figures come from the
`usage` reported by each provider; length-based estimates (including a
source's `estimated_tokens`) are for planning only and never support a savings
claim or billing.

| Success indicator (measured together) | Requirement |
|---|---|
| Total cost per successful task | Lower than an equivalent baseline |
| Input/output tokens | Actual provider-reported usage |
| Number of calls | Fewer unnecessary calls |
| Answer quality (task success, faithfulness, citation correctness) | No regression beyond the pre-declared margin |
| Context completeness (evidence recall, constraints, exceptions, dates) | No loss of required evidence |
| Latency (mean, median, p95) | Within a declared bound |
| Security and isolation | Zero accepted violations |

Compared paths on the same questions: (a) baseline RAG without
Evidence-Layer, (b) Evidence-Layer with deterministic routing, (c) adding
Decisions routing after it passes evaluation. Additional baselines: querying
every authorized source (fan-out) and full context for small collections.

**Honesty constraints.** Savings are an unproven hypothesis. Phase 5A forbids
claiming savings from a single run and the Phase 5B outcome is inconclusive;
proving the goal belongs to the Phase 1 benchmark (20–50 real tasks), which is
not done. Routing evaluation measures an intermediate metric (selection
accuracy), not end-to-end savings. Decisions must not become an always-on call
unless it earns its cost. Cost reduction alone is never sufficient: a decision
that lowers tokens but weakens quality or authorization is rejected.

## Decisions adopted now

1. **Tenant binding.** `tenant_id` in authorization contexts is derived only
   from the trusted `WorkspaceContext` built after verified membership
   (`resolve_workspace_context`). A client-supplied `workspace_id` is a
   selector, never a trusted claim. It is never accepted from a request body or
   model output. Membership and source authorization are re-verified at the
   source-read boundary. Automated tests must show that changing `workspace_id`
   or passing another workspace's source identifier reads nothing.
   **Mechanism.** Python cannot make a dataclass unforgeable, so the mechanism
   is a single trusted constructor: the only code allowed to build a
   `SourceRequestContext` takes a verified `WorkspaceContext` plus the
   authenticated `Principal`, binds the tenant to that membership, and sets a
   bounded lifetime; the host-owned policy independently checks membership.
   Any other construction site is a review failure. Until that constructor
   exists, **no allowing source policy may be shipped or wired**; the default
   policy denies everything.
2. **Default-deny transfer grants.** Source access does not authorize
   sending query text or source metadata to an external provider; these are
   separate decisions. A per-operation grant contract names provider, purpose,
   data categories, tenant, and lifetime. Only what the first real slice needs
   is built.
3. **Small, versioned source contract.** Freeze identity fields now (source
   id, workspace, type, status, access-policy reference, sensitivity,
   capabilities) with a contract version. Description, tags, language,
   freshness, and token estimates evolve with the first connector. Source
   identifiers are opaque but **not assumed unique across workspaces**: a
   routed source is always a `(tenant_id, source_id)` pair, and a consumer must
   never look a source up by its id alone. Store output is untrusted: only
   exact descriptor types with re-validated identifiers are accepted, and the
   amount read from a store or a policy is bounded.
4. **Deterministic router first.** It is the first default and the safe
   fallback. Keywords are a starting point, not evidence of sufficient routing:
   cases cover one source, several sources, and stopping when evidence is
   insufficient. No router changes access rights or opens a blocked source;
   outputs are validated and authorization is re-checked at read time. A
   routing result is a recommendation, not an authorization, and is not used
   after its context expires.
5. **Success criteria are three independent conditions:** routing quality
   (with a guard on recall of required sources), zero accepted authorization
   violations after final verification, and lower total cost without task
   quality regressing beyond a pre-declared margin. Numeric thresholds and
   margins are fixed at pilot and freeze time.

JEV remains the evidence-scoring and ranking provider and lexical remains the
fallback in the target design. OpenAI Decisions is an experimental arm for
complex routing, not the default. The caller's model writes the final answer;
no Responses-API answer generation is added.

## Decisions deferred (each with the event that settles it)

| Decision | Settled by |
|---|---|
| Practical-significance margin `M` | Pilot results plus cost of routing errors; written down before the sealed test is opened |
| Threshold `τ` and cap `k` | Tuned on development data, then frozen |
| `predicate` versus `choice` | Equal-effort development comparison; one shape goes forward |
| Final test size | After variance and within-family correlation are estimated |
| Latency and cost bounds | Declared before the final test from product requirements |
| ZDR and region eligibility | Verified against the organization's own account settings before any live transfer |
| Google Drive OAuth details | Before the connector is built, after reviewing Google's current requirements |
| Trusted grant issuer for production | Before any production transfer |

Deferred decisions must not change after final-test results are seen.

## Current state versus target

| Component | Current state | Target |
|---|---|---|
| User identity | [verified] JWT verified; `Principal(issuer, subject)` | Unchanged |
| Workspace (tenant) | [verified] client `workspace_id` resolved only through verified membership | Unchanged; feeds authorization contexts |
| Authorization contexts | [verified] untrusted unless built by a trusted host | Trusted context builder deriving tenant from membership |
| Transfer grants | [verified] deny-all; provider `jev`, purpose `evidence_scoring` only | Extended provider/purpose and a trusted issuer |
| Source reads | [verified] deny-all boundary | Trusted boundary verifying access before opening |
| Provider policy | [verified] lexical default; live JEV disabled | JEV primary once a trusted host exists |
| Source registry, router, validator, connectors | Absent on `main`. PR #37 (unmerged) proposes a local, unwired library: source contract, authorization filter, deterministic router, output validator, pipeline. A registry and connectors remain absent. | Sections below |
| Evaluation | [verified] JEV/lexical ranking only (5A/5B) | Independent security, routing, and cost evaluation |

## Implementation status

Status of each decision and control relative to PR #37, which is a draft and
not merged. "Implemented in #37" means present in that branch and tested; it
does not mean wired into any service or enabled.

| Item | Status |
|---|---|
| Source contract: opaque ids, closed enums, bounded untrusted text, version | Implemented in #37 |
| Authorization filter: tenant, routable status, trusted policy, ambiguity | Implemented in #37 |
| Untrusted store output: exact types, re-validated ids, bounded reads | Implemented in #37 |
| Deterministic router (tags, alias, description tokens only; ties by id) | Implemented in #37; `type` and `language` are not used yet |
| Output validator: exact types, one snapshot, subset of the sent catalog, no duplicates, cap | Implemented in #37; source status is enforced by the catalog filter, not the validator |
| Fail-closed pipeline: deterministic fallback or safe stop | Implemented in #37; an empty primary selection stops unless fallback is opted in |
| Results as `(tenant_id, source_id)` pairs | Implemented in #37 |
| Clock not injectable through the public API | Implemented in #37 |
| Refusing to use a routing result after its context expires | **Not implemented**: a result carries no expiry, so the consumer must check the context |
| Tenant binding from a verified `WorkspaceContext` (decision 1) | **Not implemented**; #37 takes the context as given and documents this |
| Trusted context constructor with bounded lifetime and membership check | **Not implemented** |
| Re-verification at the source-read boundary | **Not implemented** (no reader integration) |
| Timeouts around router or provider calls | **Not implemented** |
| Observability (counts, structured events) | **Not implemented** |
| Demo-data isolation and its property test | **Not implemented** |
| Use of `capabilities`, `sensitivity`, `access_policy_ref` by any logic | **Not implemented** |
| Transfer grants for routing, Decisions client, evaluation data, connectors | **Not implemented** |

## Trust boundaries and data flow (target design, not implemented)

```
client (untrusted) -> JWT auth -> Principal
  workspace_id (selector only) -> membership check (server) -> WorkspaceContext (trusted)
  source registry (workspace) -> authorization filter -> authorized catalog
  query class + cost estimate -> access-strategy choice (cheapest meeting the quality guard)
  deterministic router --(complex query + separate grant)--> Decisions
  output validator: selected ⊆ (sent ∩ authorized)
     failure -> deterministic fallback over provably eligible sources, else safe stop
  source-read boundary (re-verify before opening any source) -> connector -> retrieval
  JEV (separate grant) -> rank and reduce evidence -> caller's final LLM
```

## Identity and tenancy

Three separate concepts [proposed]: user (`Principal`), tenant/workspace
(`workspace_id`), and membership (role and status). Phase 5 is an individual
beta with a personal workspace and strict per-user isolation; teams and
multi-member workspaces are Phase 6. No full organization-management system
is built before it is needed, but the contract must not prevent it.

## Source contract and operating states

Core fields (versioned): opaque `source_id`, `workspace_id`, closed-list
`type`, `status` (`active`/`degraded`/`revoked`/`disconnected`), access-policy
reference (no content), `sensitivity` (what may be sent to a provider), and
`capabilities` (which access strategies are possible). Evolving fields:
`alias`, `description`, `scope_tags`, `language`, `freshness`,
`estimated_tokens` (planning only).

`description` and `alias` are **untrusted, tenant-controlled text**: length
limits, sanitization, treated as data not instructions. A description helps
routing but never grants access, never proves content, and never influences
authorization, even if it says "always choose this source" or "ignore access
rules".

States: (A) sources available: use them within permissions. (B) New
organization: isolated, labeled demo data. (C) No valid source in production:
report that or ask to connect a source; never fall back to demo or another
workspace's data. (D) Source failure or routing failure: deterministic
fallback over authorized sources only, else safe stop. Cold start and missing
metadata: a readiness check at connection time ("is this description enough to
route?"); when not, rely on type and tags or ask the user to pick. Per-tenant
automatic threshold tuning is not accepted before evaluation with authorized
data. Demo data is a separate, labeled tenant, and a test asserts the
production request path can never resolve demo sources.

## Source selection versus access strategy

Two separate questions: (1) which authorized sources apply? (2) which access
strategy suits each chosen source? The system checks a source's capabilities
before choosing a strategy and measures each strategy's cost and quality
independently.

Deterministic router [proposed]: inputs are the query and the authorized
catalog; output is an ordered source list from weighted token overlap between
the query and each source's scope tags, alias, and description (tags weigh
most, then alias, then the tenant-controlled description), with a fixed order
and ties broken by source id ascending. Same inputs, same outputs. Matching on
source `type` and `language` is intended but not yet implemented, and
tokenization is ASCII-only, so a non-ASCII (for example Arabic) query finds no
match and stops safely.

Output validator (applies to every router, none exempt): only an exact `list`
or `tuple` is accepted and it is copied once before any check; every returned
id is in the sent catalog, which is a subset of the authorized set; no
duplicates and a cap on count; any invented or blocked id rejects the whole
output (no partial cleaning). A source's status is enforced earlier, by the
authorization filter, so the catalog never contains a non-routable source. On
malformed output or a router error: deterministic fallback over provably
eligible sources, else a safe stop with a clear message. An empty primary
selection stops by default; falling back on empty is an explicit opt-in. A
timeout or provider error follows the same fallback path once those calls
exist, but **no timeout mechanism exists yet** and one is required before any
network-backed router is wired in. The routing fallback is separate from the
lexical scoring fallback (PRD).

| Access strategy | Position | Condition or risk |
|---|---|---|
| Query routing | Foundation | Deterministic first; Decisions a later arm |
| Hybrid search and reranking | Direct improvement after the foundation | Open question: Meilisearch or Postgres FTS; reranking is JEV's role |
| Context compression | After measurement | Must keep provenance, citations, constraints |
| Semantic caching | Deferred with conditions | Key includes tenant, ACL version, source version; revocation invalidates |
| Prompt caching | Mostly outside our control | Keep evidence ordering stable |
| Structured / text-to-SQL | Later structured connector | Parameterized templates and a read-only role; no free-form model SQL |
| Agentic retrieval | Coordinator with caps | Limits on rounds, calls, tokens |
| Long context | "No retrieval" path for small collections | Chosen only when cheaper than search |
| GraphRAG, multimodal | Deferred | High cost / outside current roadmap |
| Agent memory | A source type with owner-scoped ACL | Not this layer's responsibility |

No new path before a measurement shows the simpler one is insufficient and the
path has its own security tests. Claims about industry trends or published
vendor results are [unverified] and stay out of the design until checked at the
primary source.

## Threat model

The "Status" column is relative to PR #37 (a draft). A control marked "Not
implemented" is design only.

| Threat | Controls | Status |
|---|---|---|
| Forged or minted tenant context (any caller can build a `SourceRequestContext`) | Single trusted constructor from a verified `WorkspaceContext` and `Principal`, bounded lifetime, membership-aware policy; no tenant from client or model; RLS; isolation tests | Constructor not implemented; #37 documents and tests the assumption; default policy denies all |
| Clock injection reviving an expired context | No clock parameter in the public API | Implemented in #37 |
| Invented or cross-tenant source id | Output validator; re-check at the read boundary; opaque ids | Validator implemented in #37; read-boundary re-check not implemented |
| Non-unique source ids across workspaces | Results are `(tenant_id, source_id)` pairs; never look up by id alone | Implemented in #37 |
| Untrusted store output (subclassed or malformed descriptors, endless or huge iterables) | Exact types, re-validated identifiers, bounded reads, failure is a safe stop, module defects not masked | Implemented in #37 |
| Truncated or stale store data (for example a cached `active` status for a revoked source) | Re-verification at the read boundary against the same source version | Not implemented |
| Instruction injection in descriptions | Data not instructions; length limits; constrained outputs; adversarial tests; the validator protects authorization even if routing is misled | Implemented in #37 for the deterministic router |
| Confused deputy (router "grants") | Routers never grant; permissions come from policy; routing output is ordering only | Implemented in #37 |
| Router that tampers with its output while it is validated | Exact types and a single snapshot | Implemented in #37 |
| Slow or hung router, policy, or store | Timeouts and bounds | Bounds implemented in #37; timeouts not implemented |
| ACL change between filtering and read | Re-verify at open against the same source version | Not implemented |
| Grant replay | Per-operation grant with reserve/validate/finish and expiry | Not implemented for routing |
| Metadata leakage to a provider | Minimum data, aliases, sensitivity classes, no documents, separate grant | Not implemented |
| Demo leakage into production | Separate demo tenant; property test on the production path | Not implemented |
| Cross-tenant or stale cache | Key with tenant, ACL and source versions; invalidate on revoke | Not implemented |
| Connector token theft | Server-side only, minimum scope, immediate revocation, no logging | Not implemented |
| Cost exhaustion, agent loops | Caps on requests, rounds, budget; usage metering | Not implemented |
| Log leakage | Identifiers and digests only | Implemented in #37 for routing results (no query text, descriptions, or error messages) |

## Evaluation framework

| Layer | Covers | Oracle | Cannot prove |
|---|---|---|---|
| L1 isolation and security | Tenant boundary, blocked sources, validator, grants | Deterministic rules + property and mutation tests | Routing quality |
| L2 integration and connectors | Connect, permissions, formats, errors, recovery, revocation | Automated tests with injected faults | Routing accuracy |
| L3 robustness and stress | Missing metadata, large catalogs, changing sources, injection | Rules + boundary metrics | Routing accuracy |
| L4 routing accuracy | Choosing the right source set | Labels **independent of the router's rules** | Security (a separate gate) |

Reference answers come at three levels, and independence matters more than
whether data is synthetic or human-labeled:

| Level | How the reference is built | What it can show |
|---|---|---|
| Security tests | Deterministic permission rules | Isolation and refusal of unauthorized access |
| Synthetic routing scenarios | Scenarios independent of the deterministic router's rules, with known outcomes | Accuracy within the generated scenario space |
| Generalization set | An independent set with trustworthy references (public labeled data if suitable, or a small human-reviewed set) | How far results generalize beyond development scenarios |

L1–L3 are fully automated and need no annotators; annotators are a
prerequisite only for L4 accuracy claims. If gold labels derive from
generation rules similar to the deterministic router, the comparison is biased
toward it; passing synthetic scenarios alone never supports a claim about
real-world routing accuracy. If no suitable independent set exists, state that
real-world accuracy is not yet established. The reference is never produced by
Decisions or any model under evaluation.

A scenario generator [proposed] varies source count (3–200), domain overlap,
description quality, tenant count, share of blocked sources, and question
types. Sealed test sets are frozen per stratum, results reported per stratum,
and generalization checked by leaving a scenario out of tuning. The current
corpus (one `source_id`, 62 passages) fits L2/L3 only; the existing
`multi_source` case type means several passages from one source, not routing
between sources. No public multi-source-routing dataset with trustworthy
references has been verified [unverified].

## Decisions-routing experiment (design; live use needs separate approvals)

**API contract [verified] (official OpenAI documentation, 2026-10-20):**
`POST /v1/decisions`, public beta, model `gpt-6-luna` only, Python `openai`
>= 3.26.0. Documented request limit: **1–400 questions**, as stated in all five
official SDK references available (Python, TypeScript, Go, Java, Ruby); a
`choice` question takes 2–255 unique options. A claim of a 200-question limit
could not be corroborated and is not adopted [unverified]; the design does not
depend on either number (see below). Question types `predicate`, `choice`,
`score`; refusal is per question; the response includes `usage` and the model.
Price: input tokens only (USD 0.10 per million), with regional and long-context
adjustments whose values are not published. ZDR and HIPAA for eligible
customers; regional processing in the US and Europe. Not documented
[unverified]: latency, timeouts, rate limits, input size, determinism, default
retry behavior, `confidence` calibration, this organization's eligibility.

**Question cap.** `max_sources_per_request` is a configurable operating cap
chosen on development data, validated at run time. Above it the system either
shortlists deterministically first or splits the work across several requests;
the extra latency and cost are part of the scenarios. A provider rejection for
size is a provider failure (fallback).

**Arms.** A0 fan-out to all authorized sources; A deterministic (frozen rules,
tuned with comparable effort on development data); B Decisions `predicate` per
source; C Decisions `choice`. B and C run on development data only and one
shape goes forward.

**`choice` versus `predicate`.** Both remain experimental until compared on
development data. `predicate` scores each source independently and converts
scores to a set with a threshold `τ` and cap `k`. `choice` returns a
probability distribution over its options, from which a set can be derived the
same way; options may be single sources or explicit source sets (only for
small option counts). Probability mass splits when several sources are needed,
which can depress recall, and option design can bias results. **Pre-declared
exclusion rule:** if a `choice` arm cannot be built fairly and scalably
(without bias or option-count explosion), it is excluded from the primary
experiment instead of being forced in. Costs of all required requests count,
not one request's cost. Probabilities are not assumed comparable across arms or
calibrated: `τ` and `k` are tuned per arm by the same procedure on a
development tuning split (grouped cross-validation, a declared objective such
as `c_FN × missed + c_FP × extra` with a recall floor), calibration is checked
(reliability, Brier score, slope), and arms are compared at tuned operating
points and on rank-based metrics. `confidence` alone never decides routing or
authorization.

**Data.** Synthetic, separate from Phase 5A/5B, split by question family into
a development pilot, a development tuning split, and a sealed test set
(SHA-256 recorded before any run; no test examples in router instructions).
Gold source sets with acceptable alternatives follow the independence rules
above. Security-sensitive expected outcomes derive from a reviewed policy table
and executable property tests, not from annotator agreement.

**Metrics.** Primary: exact-match accuracy of the selected source set on the
complex query class. Guard: recall of required sources, non-inferior within a
margin set before the test. Secondary: precision of selection, invalid-source
rate, outcome distribution (`routed`, `fallback_timeout`,
`fallback_provider_error`, `fallback_malformed`, `fallback_refusal`,
`safe_stop`, `not_sent_transfer_denied`), latency (mean, median, nearest-rank
p95), tokens and estimated cost, plus end-to-end cost measures (sources
queried, candidates passed on, total tokens, the router's own cost), all per
class. Per-case logs carry identifiers and measurements only, never query
text, descriptions, secrets, or raw responses.

**Safety gates.** Blocking (structural): zero accepted violations after the
validator and the read-boundary re-check, shown by property, mutation, and
adversarial tests. Diagnostic (non-blocking): the raw invalid-proposal rate
before the validator with an exact (Clopper–Pearson) bound (about `3/n` when
none are observed); it never substitutes for structural guarantees.

**Statistics.** Family-clustered paired bootstrap, 10,000 resamples, seed
declared before the test. Test size is set by simulation over plausible
within-family correlation, discordance, effect size, number and sizes of
families, not by a closed formula alone; the design-effect approximation for
unequal cluster sizes is `1 + ((CV² + 1)·m̄ − 1)·ρ`, using an upper confidence
bound for `ρ`. Final size is the largest requirement among the primary metric,
the recall guard, and a minimum family count [after pilot]. Decision rules:
better (whole interval above `+M`), no difference, worse, inconclusive.
Margin `M` stays open until the pilot, is derived from development data and
operating costs (variance, error-cost model, extra latency/cost/exposure of
Decisions), is written down before the sealed test labels are opened, and does
not change afterward.

**Failure behavior.** No retries during evaluation, with hidden SDK retries
disabled [after pilot: verify the setting]. Refusal, malformed output, timeout,
and provider error are measured outcomes, not run stoppers (unlike Phase 5A).

**Privacy before any live call.** Inventory what is sent: a synthetic query,
an opaque source alias, type, and a short description; no documents, user
identity, or real tenant ids; `safety_identifier` opaque or omitted. A
dedicated approval for the synthetic pilot, isolated from any production path,
with call and token caps and stop conditions. ZDR and region eligibility
verified from the account. The key comes from a secret environment variable,
never a file or log. Production transfer is a separate decision.

**Freeze and default change.** Frozen before the test: shape, `τ`, `k`,
timeout, model, SDK version, seed, test digest, `M`, size, decision rules; one
run; deviations logged. Changing the default requires: zero blocking-gate
violations, accuracy interval for the complex class entirely above `+M`, recall
guard intact, declared latency and cost bounds met, human review, and a
separate explicit approval. Otherwise the deterministic router stays default.

## Google Drive and connectors

Drive is the first production connector (Phase 4): delegated authorization,
ingestion of authorized files, source identity, sync and deletion, tenant
boundaries. OAuth scopes, sync strategy, file types, retention, and error
semantics are not yet specified. [unverified] Before freezing: minimum scopes
in Google's current documentation, app-verification and security-assessment
requirements for restricted scopes, change tracking, and rate limits.
Prerequisites before enabling reads: a trusted context builder, a real
source-read boundary, immediate stop on revoked access, deletion removes
derived evidence, minimum scope, server-side tokens only. After Drive,
evaluate what was learned before choosing a second connector or generalizing a
connector framework.

## Build order, approvals, and roadmap exceptions

Order: (1) source contract, trusted identity context, authorization filter,
output validator, isolation tests; (2) deterministic router with a safe
fallback; (3) evaluation framework separating security/integration from
routing accuracy; (4) Google Drive, then review; (5) Decisions as an
additional arm after the protocol is approved.

Each step needs its own explicit approval: repository documentation (this
ADR), evaluation data and labels, first-slice implementation, live pilot on
development data, protocol freeze, one-shot test, any default change.

Roadmap exceptions (recorded here, to be reviewed at the stated trigger):
E1 start routing and isolation work before Phase 1 completes (security
infrastructure and the deterministic router are needed whatever Phase 1
shows; no production use, no default change; review when Phase 1 completes);
E2 design Phase 3 source/catalog contracts early (contracts only, no
connector generalization; review at the second connector); E3 evaluate
Decisions as an additional arm (live only with separate approvals; review
after freeze).

## What this document cannot establish

It proves no savings, quality, or production readiness. It is a design and a
set of acceptance criteria. Savings require end-to-end measurement against an
equivalent baseline in Phase 1; routing quality requires the protocol above,
run only after each approval is given.
