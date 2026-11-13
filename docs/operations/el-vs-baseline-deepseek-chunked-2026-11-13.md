# Evidence Layer vs baseline — chunked retrieve DeepSeek A/B

Date: 2026-11-13

Mode: read-only production retrieve + DeepSeek; no customer data, flags, migrations, or worker settings changed. No JEV and no vector index.

## Deployment under test

| Item | Value |
|---|---|
| Evidence Service | `https://evidence-layer-production-c4b5.up.railway.app` |
| Railway deployment | `7b50c498-cec0-4424-aacd-d9627ac6e4a6` — `SUCCESS` |
| Source commit | `853c9b088bbecc14f5c1cf44f1eca18eab5f6594` |
| Retrieval chunking | `retrieve_time_v1` |
| Chunk settings | 2,400 chars target, 240 overlap, max 48 per evidence |
| Workspace | `f67f0f38-bb05-4ec3-88a3-50b7005b0172` (personal) |
| Bridged GitHub evidence | **49 documents** |

Production retrieve was checked before the run: it returned `chunking: retrieve_time_v1`, 264 total chunks for the workspace, and chunk/path metadata.

## Protocol

| Item | Arm A | Arm B |
|---|---|---|
| Context | 49 synced docs in deterministic priority order, capped at **80,000 chars** | Production `POST /retrieve` |
| Selected baseline set | 8 documents, 78,555 chars | `top_k=8`, `max_tokens=3500`, `max_chars=14000`, `candidate_limit=100` |
| Baseline priority | README → ARCHITECTURE → Phase 8 → deletion → export → connectors → privacy → roadmap → rest | — |
| Selection | None | Lexical over chunks, path/title boosts, parent-diverse packing |
| Model | DeepSeek `deepseek-chat` | Same |
| Temperature | `0.2` | Same |
| Output cap | `max_tokens=400` | Same |
| Questions | 15-question protocol in `tests/fixtures/retrieval_harness/ab_questions.jsonl` | Same |

DeepSeek `usage.total_tokens` is authoritative. Quality labels are manual sufficiency judgments, not factuality scores.

## Per-question results

Positive delta = fewer DeepSeek tokens with EL.

| # | A tokens | B tokens | Delta | Reduction | Quality A / B | B result |
|---:|---:|---:|---:|---:|---|---|
| 1 | 18,879 | 3,610 | +15,269 | 80.9% | sufficient / sufficient — tie | 7 chunks |
| 2 | 19,141 | 3,939 | +15,202 | 79.4% | partial / partial — tie | 7 chunks |
| 3 | 19,005 | 3,830 | +15,175 | 79.8% | sufficient / partial — **loss** | 7 chunks |
| 4 | 19,131 | 3,742 | +15,389 | 80.4% | sufficient / sufficient — tie | 7 chunks |
| 5 | 19,034 | 3,325 | +15,709 | 82.5% | sufficient / sufficient — tie | 7 chunks |
| 6 | 19,076 | 3,885 | +15,191 | 79.6% | sufficient / sufficient — tie | 8 chunks |
| 7 | 18,732 | 4,000 | +14,732 | 78.6% | insufficient / sufficient — **win** | 7 chunks |
| 8 | 18,741 | 3,801 | +14,940 | 79.7% | insufficient / sufficient — **win** | 7 chunks |
| 9 | 18,767 | 3,326 | +15,441 | 82.3% | insufficient / insufficient — tie | 8 chunks |
| 10 | 18,840 | 3,384 | +15,456 | 82.0% | sufficient / sufficient — tie | 7 chunks |
| 11 | 18,838 | 3,431 | +15,407 | 81.8% | partial / sufficient — **win** | 7 chunks |
| 12 | 19,110 | 3,312 | +15,798 | 82.7% | sufficient / sufficient — tie | 8 chunks |
| 13 | 18,941 | 3,764 | +15,177 | 80.1% | sufficient / sufficient — tie | 7 chunks |
| 14 | 18,799 | 3,635 | +15,164 | 80.7% | insufficient / insufficient — tie | 7 chunks |
| 15 | 19,131 | 3,787 | +15,344 | 80.2% | sufficient / sufficient — tie | 8 chunks |

All 15 questions used fewer tokens with EL.

## Aggregates

| Metric | Arm A | Arm B |
|---|---:|---:|
| Mean total tokens | **18,944.3** | **3,651.4** |
| Median total tokens | 18,941.0 | 3,742.0 |
| Mean prompt tokens | 18,733.4 | 3,412.7 |
| Mean completion tokens | 210.9 | 238.7 |
| Mean reduction | — | **80.7%** |

Retrieve-side mean selected context was **3,445.8 estimated tokens**. B selected **7.27 chunks/question** on average. Eleven questions ended `budget_exhausted` after 7 chunks; four returned `ok` with 8 chunks. The production char/token limits were not exceeded.

## Quality: B vs A

**B wins 3 / ties 11 / loses 1.**

Previous whole-document report: 7 wins / 4 ties / 4 losses. Chunking did not simply increase wins; it converted most prior losses into ties and reduced losses from four to one.

### Q1 — product definition: tie (previously a loss)
- B selected `README.md` plus PRD, roadmap, and architecture ADRs.
- B answered the product and the problem correctly, including the MCP/SaaS framing.
- The previous failure — README not selected at all — is fixed.

### Q2 — architecture: tie, both partial
- B selected `apps/web/README.md`, `docs/ARCHITECTURE.md`, and the Evidence Service README.
- B recovered the web/Evidence Service split and Vercel/Railway placement, but the 400-token answer cap truncated it before Supabase and Railway responsibilities were complete.
- Both answers repeat stale text saying hosted retrieval/scoring is not part of the service. That is a source-freshness problem, not a retrieval miss.

### Q13 — deletion worker: tie (previously a loss)
- B selected the current implementation audit and deletion closure docs.
- B answered operation (one replica, advisory lock, worker image, `run_account_deletion_slice`) and durability gaps (no documented lease/retry/alerting/`purge_failed` runbook; journal durability deferred).
- A also answered sufficiently, so this is a tie rather than a forced win. It is no longer a regression.

### Other notable changes
- **Q7 personal API keys — win:** A returned insufficient context; B answered key generation, scopes, expiry, limits, revocation, and RPC isolation.
- **Q8 retrieve mechanics — win:** A returned insufficient context; B answered candidate windows, Lexical scoring, token/char budgets, estimator, and JEV exclusion.
- **Q11 GitHub limits — win:** A reported a stale 25-document limit; B reported 100 documents, 2 MiB/document, 12 MiB/sync, 65,536 chars/evidence, and noted the conflicting docs.
- **Q3 Phase 8 — loss:** A gave the full C3–C7 decision table; B returned only a partial C3/C5/C7 summary and omitted details such as C5's bounded-worker evidence. This is the one remaining regression.

## Interpretation

- The chunk/path selector preserved the token discipline: **80.7% mean reduction**, with every question below the baseline.
- It fixed the two explicit product/worker failures (Q1, Q13) by selecting README and current deletion/worker chunks.
- It introduced no broad quality collapse: 11 ties, 3 wins, 1 loss.
- The remaining losses are source/answer-budget effects rather than budget blowups: Q3 needs more Phase 8 detail than the 400-token answer allowed, and Q2 needs fresher architecture text.
- `budget_exhausted` still appears on 11/15 because 8 chunks at ~500–800 tokens cannot fit 3,500 tokens. It selected 7 chunks instead of failing or expanding the corpus.

## Limits

- Baseline is bounded at 80,000 chars (8 documents), not the full 477,725-char corpus.
- DeepSeek answers are capped at 400 output tokens; Q2 and Q3 hit that cap.
- Fifteen questions, one model/temperature, and manual sufficiency labels.
- No JEV was enabled and no vector index was used.
- The corpus contains stale architecture/retrieval text; retrieval cannot repair source drift.
- `budget_exhausted` remains common at `top_k=8`/3,500 tokens.

## Re-run

Use the same workspace and production endpoint with:

```bash
python scripts/measure_retrieval_reduction.py \
  --corpus /tmp/github-synced-corpus.jsonl \
  --queries tests/fixtures/retrieval_harness/ab_questions.jsonl \
  --api-url "$EL_API_URL" \
  --api-key "$EL_API_KEY" \
  --workspace-id "$EL_WORKSPACE_ID" \
  --top-k 8 --max-tokens 3500 --max-chars 14000 --candidate-limit 100
```

The harness is retrieval-only by default. Use the same DeepSeek `deepseek-chat`, temperature `0.2`, and 400-output-token protocol for answer-quality comparison. Do not force a GitHub re-sync for chunking; it is retrieve-time.

Read-only report; no production data, flags, migrations, or worker settings were changed.
