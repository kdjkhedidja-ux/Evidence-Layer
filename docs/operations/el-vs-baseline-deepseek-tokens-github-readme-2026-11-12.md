# Evidence Layer vs baseline — GitHub README DeepSeek comparison

Date: 2026-11-12
Mode: production retrieve + DeepSeek; no customer data, no flag or migration changes.

## Sync verification

| Check | Result |
|---|---|
| GitHub connection | `github: connected` |
| Last GitHub sync | 2026-11-12 23:33 UTC |
| Synced GitHub documents | 5 |
| Synced README | 24,821 chars, present in `connector_documents` |
| Stored representation at run time | base64 for README and other text files; `content_text` was null |
| Workspace evidence for `EL_WORKSPACE_ID` | 0 |

The GitHub sync itself succeeded, but text files were stored as base64 because GitHub returns `application/vnd.github.raw`. That content-type handling is fixed and deployed; a new GitHub **Sync** is required to repopulate `content_text`.

Connector documents are not `evidence_records`, so they are not directly visible to the retrieve endpoint. For this experiment, a synthetic test workspace was seeded with 10 chunks extracted from the **actual GitHub-synced README**, and that account was purged afterward. This is real GitHub repository content, but not yet the live connector→workspace→retrieve product flow.

## Setup

| Item | Value |
|---|---|
| Evidence Service | `evidence-layer-production-c4b5.up.railway.app` |
| Retrieve | `top_k=5`, `max_tokens=2500`, `max_chars=10000` |
| Selection | Lexical, no JEV |
| DeepSeek | `deepseek-chat`, temperature `0.2`, max output `320` |
| Arm A | Full GitHub README capped at 16,000 chars + public product pages; no retrieve call |
| Arm B | Only retrieved chunks from the GitHub-synced README |
| Questions | 10 |

## Per-question results

DeepSeek `usage.total_tokens` is the source of truth. Positive delta = fewer tokens with EL.

| # | Question | A tokens | B tokens | Delta | Delta % | Quality A / B | B docs |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | What is Evidence Layer in one paragraph? | 3983 | 2094 | +1889 | 47.4% | sufficient / sufficient | 5 |
| 2 | How does account deletion and purge work at a high level? | 3920 | 1325 | +2595 | 66.2% | insufficient / partial | 3 |
| 3 | How does account export work, including download grants and expiry? | 3920 | 1297 | +2623 | 66.9% | insufficient / partial | 3 |
| 4 | What connectors exist, and which are live versus coming soon? | 4050 | 2064 | +1986 | 49.0% | sufficient / sufficient | 4 |
| 5 | How do personal API keys work for AI agents? | 3981 | 2042 | +1939 | 48.7% | partial / partial | 5 |
| 6 | How does retrieve/select evidence work with Lexical scoring and budgets? | 3922 | 1989 | +1933 | 49.3% | insufficient / insufficient | 5 |
| 7 | What does the ROI and cost calculator claim about tokens versus quality? | 3921 | 2102 | +1819 | 46.4% | insufficient / insufficient | 5 |
| 8 | What are the privacy and Google Drive data-deletion policy highlights? | 3921 | 2373 | +1548 | 39.5% | insufficient / insufficient | 5 |
| 9 | How do I connect GitHub as a source? | 3956 | 2234 | +1722 | 43.5% | sufficient / sufficient | 5 |
| 10 | What are the known limits of the retrieval API, connectors, and worker? | 4015 | 2288 | +1727 | 43.0% | partial / partial | 5 |

## Aggregates

| Metric | Arm A | Arm B |
|---|---:|---:|
| Mean total tokens | **3958.9** | **1980.8** |
| Median total tokens | 3939.0 | 2079.0 |
| Mean prompt tokens | 3916.8 | 1927.4 |
| Mean completion tokens | 42.1 | 53.4 |
| Mean total-token reduction | — | **50.0%** |

Retrieve metadata averaged **1,892 estimated context tokens** per question. The API estimator was `chars_div_4`; DeepSeek usage is authoritative.

## Quality

- **B vs A:** **2 wins / 8 ties / 0 losses**.
- B improved deletion and export answers because the selected README chunks contained those sections; the 16k Arm A cap cut them off.
- Ties: product definition, connectors, API keys, GitHub setup, and limits had comparable sufficiency.
- Both arms were insufficient for retrieval mechanics, ROI claims, and privacy/Google Drive. The README does not contain enough detail for those questions.
- No question had a clear B quality regression on this corpus.

## Limits

1. The experiment uses **README content only**; the GitHub sync currently stores 5 files and the repository `docs/` corpus was not synced.
2. Connector documents are not evidence records; the live connector→workspace→retrieve flow is not yet implemented.
3. Candidate window is bounded by the retrieve API (default 50, max 100).
4. Lexical-only selection; JEV is not used.
5. Token metadata is estimated (`chars_div_4`) when the pinned tokenizer artifact is unavailable.
6. DeepSeek usage is the source of truth for the comparison; no tokenizer-perfect claim is made.

## Conclusion

On the actual GitHub-synced README corpus, EL reduced mean DeepSeek total tokens from **3,958.9 to 1,980.8**, a **50.0% reduction**, with no measured quality losses and two quality improvements (deletion and export). The result supports the retrieval path, but the corpus is still README-only and the connector→evidence bridge is missing.

Next steps: re-sync GitHub after the text-content fix, sync a non-empty repository with the `docs/` tree, bridge connector documents into workspace evidence (or extend retrieve deliberately), then repeat this 10-question comparison.
