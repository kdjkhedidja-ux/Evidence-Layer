# Phase 6 Readiness Gate

Status date: 2026-11-06 (historical gate). This gate consolidates local Phase 6 work and distinguishes frozen offline contracts from operational/provider approval. It does not authorize provider calls, JEV, or Production. Current roadmap status is governed by [`ADR 0011`](adr/0011-merge-phase1-validation-into-phase2-comparison.md); later Phase 7 automated judge runs are documented separately and remain non-confirmatory. Production JEV activation is recorded separately in [`provider-jev-activation-2026-11-14.md`](operations/provider-jev-activation-2026-11-14.md) and does not retroactively authorize Real-World v1 egress.

## Current status

| Area | Status | Evidence / boundary |
|---|---|---|
| Specification and scientific protocol | CLOSED | `docs/phase6-multi-model-benchmark-protocol.md`; separates evidence quality, answer quality, and operational economics. |
| Real-World v1 artifact integrity | PASS | 51 passages, 40 cases; deterministic lexical results verify (`scripts/verify_real_world_benchmark.py --verify`). This is not independent judgment validation or a blind confirmatory test. |
| Offline Usage and cost accounting | PASS | `src/evidence_layer/usage_measurement.py`; actual/estimated/unknown, exact pricing-version pinning, cache read/write categories, and fail-closed applicability. |
| Offline evidence arms | PASS | `scripts/run_phase6_offline_arms.py`; Lexical and Evidence Layer run locally with stable manifests/results/usage. |
| Synthetic provider parsers and fake orchestration | PASS | `src/evidence_layer/provider_usage.py`, `src/evidence_layer/provider_execution.py`, and synthetic tests. No SDK clients or network calls. |
| Offline Fake E2E integrity | PASS | 40 cases, two evidence arms, three virtual models, 240 answer rows, 241 attempts, 321 Usage events; files and hashes reproduce byte-identically (`docs/phase6-offline-fake-e2e-pipeline.md`). |
| Candidate Roster v1 | FROZEN AS 8-MODEL CANDIDATE LIST | Three Anthropic, three OpenAI, two DeepSeek; Astra excluded. The candidate list is frozen, but it is not an executable/frozen Model Matrix. |
| Operational Model Matrix | DESIGN REVIEWED; NOT FROZEN | Proposed profiles and public API constraints are consolidated in `docs/phase6-operational-model-matrix-design.md`; per-model hard total output caps, pinned tokenizer for visible <=800 (usage fields now implemented, see `docs/phase6-usage-schema-extension.md`), returned IDs, SDK schema, region/retention, and authorized test profile remain pending. |
| Pricing Registry | NOT APPROVED | Candidate snapshots exist, but effective dates are unknown and DeepSeek write-billing applicability remains unknown. |
| Actual provider usage/cost | NOT ESTABLISHED | Fake usage is estimated; no provider response has been received. |
| Answer quality comparison | NOT STARTED | Fake answers are not evaluated; no real answer model has run. |
| Egress authorization | PENDING; CLOSED BY DEFAULT | Requires separate data-transfer/security approval per provider/environment/dataset. Candidate acceptance is not consent to send benchmark data. |
| Real SDK integration | NOT IMPLEMENTED | Only parsers and a fake-client protocol/harness exist. |
| JEV evaluation | NOT AUTHORIZED | JEV arm remains `NOT_RUN`; no JEV calls. |
| Production | NOT AUTHORIZED | No production configuration, calls, or changes. |

## What can be frozen now

The following offline/reference artifacts can be version-frozen without provider access:

- **Real-World v1 reference:** dataset/split fingerprints and historical lexical result; preserve existing artifacts byte-for-byte. Do not describe it as a blind confirmatory set.
- **Offline protocol:** evidence/answer/operations separation, paired-case rules, metrics, failure denominators, and no-JEV default (`docs/phase6-multi-model-benchmark-protocol.md`).
- **Candidate Roster v1:** the frozen eight reviewed API-ID candidates and Astra exclusion. The candidate list is not `approved_test` and does not freeze generation settings or the operational Model Matrix.
- **Offline evidence arm configuration:** current `lexical_rag` and `evidence_layer` policy/version, run schema, fingerprints, and local Usage contract.
- **Fake integration contract:** attempt IDs/ordinals, retries, safe error categories, returned-model field semantics, and hash/replay behavior. Fake output remains explicitly not evaluated.

Do not freeze official model API behavior based solely on marketing/model-list pages. The candidate-specific generation settings remain proposed until reviewed against the selected endpoint's current API contract.

## Human decisions required

These decisions cannot be inferred from public docs or local tests:

1. Approve each candidate's generation profile: reasoning mode/effort, hard total output cap separate from the visible <=800 target, sampling controls, tools, retention/storage options, and acceptable provider region.
2. Decide whether test data may leave the local environment, and which dataset class is permitted per provider. Approval must name provider, endpoint, region, data classes, retention policy, and purpose.
3. Decide the independent answer-grading and blind-review process, including whether a new sealed confirmatory set is required before inferential claims.
4. Approve the pricing snapshot as a test reference only after effective dates and all applicable token/cache tiers are evidenced. This is not billing approval.
5. Resolve DeepSeek cache-write billing applicability from an authoritative source or keep DeepSeek cost `unknown`.

## Provider validation required after authorization

Only after the above approvals may a separate controlled test validate:

- requested versus returned model IDs/versions; missing or mismatched returned IDs fail candidate pooling and are never substituted;
- exact request/response schema for each SDK/endpoint, including stream completion and partial-response behavior;
- cache hit/write counters, TTL breakdown equations, provider-specific null/omission behavior, and total-input equations;
- provider/model-specific context/output caps, reasoning controls, storage/retention controls, and region;
- transport/SDK retries are disabled or fully surfaced, so each call maps to one attempt and one Usage event;
- synthetic/non-sensitive request cost recomputes from the serialized event and pinned pricing version; missing/unknown data stays null;
- safe logging, secret-manager injection, request limits, and opt-out/stop behavior.

Provider validation must begin with the approved synthetic/non-sensitive set. Real-World v1 egress requires its own explicit approval; the exposed test split is not an independent blind test.

## Hard blockers to actual comparison

- Model Matrix operational settings and returned-model verification are not frozen.
- Pricing Registry is candidate-only; effective dates and DeepSeek write-billing applicability remain unresolved.
- There are no real provider UsageEvents, Actual Cost results, answer-quality results, or measured provider latency.
- Real SDK clients and provider egress approvals do not exist.
- Independent blind review/confirmatory data is pending for any scientific superiority claim.
- JEV remains separately unauthorized and out of scope for the initial model/usage validation.

## Stop conditions

Do not start external evaluation if any requested model/price/policy is unresolved, if egress approval is absent, if a provider silently returns another model, if cache usage violates the documented accounting equation, if raw content/secrets would be logged, or if any required cost/quality field would be guessed or turned into zero. Record the attempt as blocked/unknown and keep the provider and JEV gates closed.

## Gate verdict

**LOCAL PIPELINE READY; EXTERNAL EVALUATION NOT READY / NOT AUTHORIZED.** Candidate Roster v1 is frozen as the eight-model candidate list. The operational Model Matrix and Pricing Registry are not frozen/approved. No provider, JEV, or Production call is authorized by this readiness gate.

## Freeze record

Operational Model Matrix v1 — local contract is frozen as described in `docs/phase6-operational-model-matrix-v1.md`. Returned model, real response schema, SDK request shapes, egress approval, and price effective dates remain pending.
