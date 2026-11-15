# Evidence Layer vs baseline — GitHub-synced corpus DeepSeek comparison

Date: 2026-11-13
Mode: read-only production evidence/retrieve + DeepSeek; no customer data was modified, no flags or migrations changed.

## Sync verification

| Check | Result |
|---|---|
| Workspace | `f67f0f38-bb05-4ec3-88a3-50b7005b0172` (personal) |
| Bridged GitHub evidence | **49 documents** |
| Connector documents | 50 listed / 49 ingested / 1 skipped in the bridge smoke |
| Retrieve candidate source | Evidence records bridged from the GitHub connector |
| Baseline source | Same 49 evidence documents read through the API-key evidence list; **no retrieve call** |

The updated API key and workspace ID resolve to the bridged personal workspace. This run uses the real GitHub-synced corpus, not a synthetic seed.

## Setup

| Item | Value |
|---|---|
| Evidence Service | `evidence-layer-production-c4b5.up.railway.app` |
| Arm A | 49 evidence documents concatenated by priority, capped at **80,000 chars**; no retrieve |
| Arm B | `POST /retrieve`, `top_k=8`, `max_tokens=3500`, `max_chars=14000`, `candidate_limit=100` |
| Selection | Lexical, JEV not used |
| DeepSeek | `deepseek-chat`, temperature `0.2`, max output `400` |
| Questions | 15, spanning product, architecture, Phase 8, deletion, export, connectors, API keys, retrieval, ROI, privacy, limits, security, worker, MCP, and risks |

All Arm B calls returned `budget_exhausted` while still selecting 1–4 documents because the largest selected documents exceeded the remaining character/token budgets. This is a context-budget behavior, not an empty result.

## Per-question results

DeepSeek `usage.total_tokens` is the source of truth. Positive delta = fewer tokens with EL.

| # | Question | A tokens | B tokens | Delta | Delta % | Quality A / B | B docs |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | What is Evidence Layer and what problem does it solve? | 18815 | 3354 | +15461 | 82.2% | sufficient / insufficient | 3 |
| 2 | Current architecture and responsibilities of web, service, Supabase, Railway | 18876 | 3518 | +15358 | 81.4% | sufficient / partial | 2 |
| 3 | Phase 8 decisions for C3–C7 | 18483 | 3490 | +14993 | 81.1% | insufficient / sufficient | 2 |
| 4 | Account deletion and purge end to end | 18469 | 3580 | +14889 | 80.6% | insufficient / sufficient | 2 |
| 5 | Account export, grants, expiry, revocation | 18473 | 3432 | +15041 | 81.4% | insufficient / sufficient | 2 |
| 6 | Connectors live/coming soon and the GitHub bridge | 18872 | 2864 | +16008 | 84.8% | sufficient / partial | 1 |
| 7 | Personal API keys for agents | 18468 | 3693 | +14775 | 80.0% | insufficient / sufficient | 3 |
| 8 | Retrieve/select, Lexical, windows, budgets | 18479 | 3942 | +14537 | 78.7% | insufficient / sufficient | 4 |
| 9 | ROI/cost calculator claims and non-claims | 18475 | 2920 | +15555 | 84.2% | insufficient / insufficient | 2 |
| 10 | Privacy and Google Drive data-deletion | 18824 | 3350 | +15474 | 82.2% | sufficient / sufficient | 3 |
| 11 | GitHub ingestion/bridge limits | 18831 | 4022 | +14809 | 78.6% | sufficient / sufficient | 4 |
| 12 | Cross-tenant/ownerless-write controls | 18867 | 2863 | +16004 | 84.8% | sufficient / sufficient | 2 |
| 13 | Deletion worker operation/durability | 18621 | 3119 | +15502 | 83.3% | sufficient / insufficient | 2 |
| 14 | MCP server and API-key retrieve path | 18649 | 3848 | +14801 | 79.4% | insufficient / sufficient | 3 |
| 15 | Remaining Phase 8/production risks | 18869 | 3479 | +15390 | 81.6% | partial / sufficient | 2 |

## Aggregates

| Metric | Arm A | Arm B |
|---|---:|---:|
| Mean total tokens | **18,671.4** | **3,431.6** |
| Median total tokens | 18,649.0 | 3,479.0 |
| Mean prompt tokens | 18,470.1 | 3,142.8 |
| Mean completion tokens | 201.3 | 288.8 |
| Mean total-token reduction | — | **81.6%** |

Retrieve metadata averaged **3,249 estimated context tokens** per question. The API estimator was `chars_div_4`; DeepSeek usage is authoritative.

## Quality assessment

**B vs A: 7 wins / 4 ties / 4 losses.**

Wins for B:
- Phase 8 scope decisions
- account deletion
- account export
- personal API keys
- retrieve mechanics
- MCP integration
- remaining risks

Ties:
- privacy/Google Drive
- GitHub limits
- cross-tenant controls
- ROI (both insufficient)

Losses for B:
- **Product definition:** B returned “insufficient context”; the selected docs were `api-retrieval-mcp`, `github-sync-and-retrieve`, and a restore checklist, not the README/product definition.
- **Architecture:** B’s selected docs were service/web READMEs and produced a partially stale/synthetic-explorer description.
- **Connector bridge:** B selected one document and did not explain the bridge.
- **Deletion worker:** B selected older deletion closure documents and returned insufficient context.

This is the central finding: the 81.6% token reduction is real, but lexical retrieval over whole documents is not consistently selecting the best source. Long-document scoring, missing chunking, and lack of path/title boosts cause quality losses on important questions.

## Limits

1. Baseline is capped at 80,000 chars out of a 477,725-char corpus; it is a bounded, not full-context, baseline.
2. Arm B is capped at 14,000 chars / 3,500 estimated tokens; all calls hit the budget while selecting 1–4 documents.
3. Evidence records are whole documents, not chunks; long README/architecture files dilute lexical scoring and consume budget.
4. Lexical-only selection; no semantic, heading, title, path, or recency weighting beyond deterministic scoring.
5. Retrieve metadata uses `chars_div_4`; DeepSeek usage is the source of truth.
6. Fifteen questions and one model/temperature; quality labels are manual sufficiency judgments, not factuality scores.

## Conclusion

On the real 49-document GitHub-synced corpus, EL reduced mean DeepSeek total tokens from **18,671.4 to 3,431.6**, an **81.6% mean reduction**, with all 15 questions using fewer tokens. Quality was mixed: B won 7, tied 4, and lost 4. Token reduction is strong; **quality parity is not yet established**.

Before claiming production agent reliability, improve retrieval selection for long documents: chunk evidence, boost README/architecture/title/path matches, and consider a fallback to a broader candidate set for low-confidence queries. Then repeat this 15-question run with the same corpus.

Read-only report; no production data, flags, migrations, or worker settings were changed.
