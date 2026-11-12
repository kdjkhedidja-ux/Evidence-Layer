# Evidence Layer vs baseline — DeepSeek token comparison

Date: 2026-11-12
Mode: read-only production retrieval + DeepSeek chat inference; no customer data was used, no flags or migrations were changed.

## Setup

| Item | Value |
|---|---|
| Evidence Service host | `evidence-layer-production-c4b5.up.railway.app` |
| Retrieve endpoint | `POST /v1/personal-api-keys/workspaces/{workspace_id}/retrieve` |
| Retrieve auth | Personal API key, workspace-scoped, `evidence:read` |
| Selection | Lexical (`lexical_stopword_filtered`), JEV not used |
| Retrieve budgets | `top_k=5`, `max_tokens=2500`, `max_chars=10000`, default candidate window |
| DeepSeek endpoint | `https://api.deepseek.com/chat/completions` |
| DeepSeek model | `deepseek-chat` |
| Temperature | `0.2` for both arms |
| DeepSeek max output | `320` tokens for both arms |
| System prompt | Answer only from provided context; say “insufficient context” if needed |
| Arm A context | 16,000-character cap assembled from repository product docs plus public `evidencelayer.dev` pages; no retrieve call |
| Arm B context | Only evidence returned by the production retrieve endpoint |
| Questions | 10 topics covering product, deletion, export, connectors, API keys, retrieval, ROI, privacy, GitHub, and limits |

### Corpus note

The pre-existing `EL_WORKSPACE_ID` workspace contained **0 evidence records**, so Arm B would otherwise have been empty. To execute a meaningful comparison, a dedicated synthetic account/workspace was seeded with 10 excerpts from this repository's public/product documentation (the same kind of material a GitHub documentation sync would ingest). The account was purged through the normal deletion worker after the run. This is a **repo-doc corpus**, not a live customer or live GitHub-synced corpus measurement.

## Per-question results

DeepSeek `usage.total_tokens` is the source of truth for both arms. Delta is Arm A minus Arm B; a positive delta means EL used fewer total tokens.

| # | Question | A total tokens | B total tokens | Delta | Delta % | Quality A / B | B evidence docs |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | What is Evidence Layer in one paragraph? | 3721 | 2251 | +1470 | 39.5% | sufficient / sufficient | 4 |
| 2 | How does account deletion and purge work at a high level? | 3724 | 2560 | +1164 | 31.3% | sufficient / sufficient | 4 |
| 3 | How does account export work, including download grants and expiry? | 3884 | 2373 | +1511 | 38.9% | sufficient / sufficient | 4 |
| 4 | What connectors exist, and which are live versus coming soon? | 3729 | 1922 | +1807 | 48.5% | sufficient / sufficient | 3 |
| 5 | How do personal API keys work for AI agents? | 3872 | 2415 | +1457 | 37.6% | sufficient / sufficient | 4 |
| 6 | How does retrieve/select evidence work with Lexical scoring and budgets? | 3798 | 2376 | +1422 | 37.4% | sufficient / sufficient | 4 |
| 7 | What does the ROI and cost calculator claim about tokens versus quality? | 3681 | 2013 | +1668 | 45.3% | sufficient / sufficient | 3 |
| 8 | What are the privacy and Google Drive data-deletion policy highlights? | 3656 | 1849 | +1807 | 49.4% | insufficient / insufficient | 3 |
| 9 | How do I connect GitHub as a source? | 3750 | 2429 | +1321 | 35.2% | sufficient / partial | 4 |
| 10 | What are the known limits of the retrieval API, connectors, and worker? | 3939 | 2605 | +1334 | 33.9% | sufficient / partial | 4 |

## Aggregates

| Metric | Arm A (without EL) | Arm B (with EL) |
|---|---:|---:|
| Mean total tokens | **3775.4** | **2279.3** |
| Median total tokens | 3739.5 | 2374.5 |
| Mean prompt tokens | 3651.8 | 2181.3 |
| Mean completion tokens | 123.6 | 98.0 |
| Mean total-token reduction | — | **39.7%** |

Retrieve metadata averaged **2,293 estimated context tokens** across the 10 questions, with 3–4 documents selected per question. The API token estimator was `chars_div_4` during this run, so retrieve-side token metadata is an estimate; DeepSeek `usage` is the authoritative comparison.

## Quality notes

- **Arm A:** 9 sufficient, 1 insufficient. It answered broad product, deletion, export, connector, API-key, retrieval, ROI, GitHub, and limits questions from the full capped corpus.
- **Arm B:** 7 sufficient, 2 partial, 1 insufficient. The selected context was sufficient for most product and control questions.
- **Question 8 (privacy/Google Drive):** both arms returned “insufficient context.” The privacy material was present in the baseline corpus but not selected/used well by either prompt. Do not claim a privacy-policy win from this run.
- **Question 9 (connect GitHub):** Arm A included more procedural setup detail; Arm B was partial because the selected evidence did not contain step-by-step setup instructions.
- **Question 10 (known limits):** Arm A produced a more complete consolidated limits summary; Arm B listed several limits but called the list incomplete.
- No claims are made about answer factual correctness beyond the provided-context sufficiency labels above.

## Limits and caveats

1. **Synthetic repo-doc corpus:** this run used a synthetic workspace seeded from repository/product docs because the configured workspace had zero evidence. It is not a live GitHub-synced customer measurement.
2. **Retrieval window:** the API reads the newest candidate window (default 50, max 100), not the entire workspace.
3. **Lexical-only:** selection is word-overlap lexical scoring; no semantic or JEV ranking.
4. **Token metadata:** retrieve uses `chars_div_4` when the pinned tokenizer artifact is unavailable. DeepSeek usage is the source of truth for the comparison table.
5. **Prompt quality:** one fixed system prompt and one temperature were used; results may vary with prompt design or model version.
6. **Privacy question:** both arms failed; this is a quality regression risk for privacy documentation even though token usage improved.

## Conclusion

On this 10-question set, Evidence Layer reduced mean DeepSeek total tokens from **3,775.4 to 2,279.3**, a **39.7% mean reduction**, with Arm B winning on tokens for all 10 questions. Quality was mostly preserved (7 sufficient vs 9 sufficient), but there were real regressions: both arms failed the privacy/Google Drive question, and Arm B was less complete for GitHub setup and the consolidated limits question.

This supports enabling the API-key retrieve path for bounded product Q&A, but not as a universal replacement for a comprehensive product corpus. The next measurement should use a non-empty GitHub-synced repository and a corpus export from `connector_documents`, with the privacy and setup questions retained as quality gates.
