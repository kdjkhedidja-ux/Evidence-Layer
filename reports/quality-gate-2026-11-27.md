# Evidence Layer quality gate — 2026-11-27

50 questions, 8 arms, run 2026-11-27 11:30–11:48 UTC. No production setting was
changed for this run; retrieval ran locally with the production ranking code
over a frozen snapshot of this repository.

## Summary

| | EL, boost off (JEV) | EL, boost on (JEV) | EL lexical only | Baseline A: 3 best files | Baseline B: classic RAG (BM25) | Closed book |
|---|---|---|---|---|---|---|
| Evidence recall@5 (45 answerable) | **0.22** (10/45) | **0.27** (12/45) | 0.22 (10/45) | **0.47** (21/45) | 0.31 (14/45) | — |
| Correctness + completeness vs A (0–4, Δ, 95% CI) | −0.61 [−1.12, −0.10] | −0.76 [−1.29, −0.26] | — | — | — | — |
| Correctness + completeness vs B (0–4, Δ, 95% CI) | −0.31 [−0.79, +0.12] | −0.21 [−0.64, +0.17] | — | — | — | −2.11 [−2.66, −1.59] |
| Answer input tokens vs A (paired) | −88.3% | −90.3% | −87.4% | — | — | — |
| Answer input tokens vs B (paired) | −11.0% | −24.8% | −2.3% | — | — | — |
| Abstained on 5 unanswerable | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

**Conclusion, with the limits below:** on this question set Evidence Layer
retrieves the required evidence **less often than both baselines**. Its answers
are **worse than pasting the three best-matching files** (the confidence interval
excludes zero) and **statistically tied with classic BM25 RAG**, while using
**about 88–90% fewer answer input tokens than the files baseline** and 11–25%
fewer than classic RAG. The identifier boost raises recall on code-identifier
questions (6/10 → 8/10) and does not change answer quality measurably. All arms
abstained correctly on the 5 unanswerable questions.

The main cause is diagnosed below: the JEV re-ranker only ever sees the 2–6
chunks the lexical stage already selected, so it cannot improve recall, and the
lexical first stage misses most plain-language questions (1/22).

## Setup

| Item | Value |
|---|---|
| Corpus | This repository at commit `b71856f`, filtered with the GitHub connector's own path rules (714 files), indexed exactly as production (large files in parts) |
| Secret scan before any transfer | gitleaks v8.21.2 (`--redact`) + 11 project patterns; 3 files matched and were excluded: `apps/web/tests/config-and-secrets.test.ts`, `src/evidence_layer/tokenization.py`, `tests/test_evidence_service_config.py` |
| Frozen corpus | 711 files, snapshot SHA-256 `867ff504d8207920…` |
| Frozen questions | v2, SHA-256 `886f63953feefa217edce3380a3a418f161599692e9fbeaba48e0e36c79da426`, frozen before the run (list in the appendix) |
| Question mix | 10 code-identifier, 13 product, 13 operations, 9 multi-file, 5 unanswerable; 27 plain-language, 23 technical |
| Gold | Facts (strings) with alternative wordings, combined AND/OR; any file of the snapshot containing the fact is an acceptable source; whitespace-normalised substring match |
| Retrieval budget (EL and B) | top_k 5, 1,200 tokens, 6,000 characters |
| Answer model | DeepSeek `deepseek-v4-pro` (Responses API, max 800 output tokens, low reasoning effort); it never saw the reference answers |
| Judge | OpenAI `gpt-6.1-sol`, blind to the arm, answer order randomised per question and pairing; sees the question, a reference answer derived from the gold sources, the gold facts, and each answer with its own evidence |
| JEV | Typesafe Jev 1.14 via `typesafe-sdk 0.7.2`, production code path (`_retrieval_response`), run twice |

### Arms

| Arm | Description |
|---|---|
| `el_off_jev_r1` / `r2` | Production retrieval with JEV, identifier boost off (today's production behaviour), run 1 and run 2 |
| `el_on_jev_r1` / `r2` | Same with `EVIDENCE_IDENTIFIER_BOOST_ENABLED` on |
| `el_off_lex` | Production retrieval, lexical only, boost off (reference) |
| `base_files` (A) | BM25 over whole files; the 3 best files concatenated, capped at 24,000 tokens |
| `base_rag` (B) | Classic RAG: BM25 over the same chunks, top 5 within the same 1,200-token / 6,000-character budget, no boosts |
| `closed_book` | No evidence (secondary reference) |

Neither baseline used the gold labels in any way: both rank from the question
text and the snapshot only.

## Results

### Evidence recall@5 (a question counts when its gold facts are in the selected evidence)

| Arm | All (45) | Code-identifier (10) | Product (13) | Ops (13) | Multi-file (9) | Plain (22) | Technical (23) |
|---|---|---|---|---|---|---|---|
| `el_off_jev_r1` | 10 | 6 | 1 | 3 | 0 | 1 | 9 |
| `el_off_jev_r2` | 10 | 6 | 1 | 3 | 0 | 1 | 9 |
| `el_on_jev_r1` | 12 | 8 | 1 | 3 | 0 | 1 | 11 |
| `el_on_jev_r2` | 12 | 8 | 1 | 3 | 0 | 1 | 11 |
| `el_off_lex` | 10 | 6 | 1 | 3 | 0 | 1 | 9 |
| `base_files` | 21 | 8 | 6 | 5 | 2 | 6 | 15 |
| `base_rag` | 14 | 6 | 4 | 3 | 1 | 2 | 12 |

JEV stability between the two runs: identical selections on 44/50 questions
(boost off) and 43/50 (boost on); recall identical in both runs. No JEV
fallback occurred (all 200 rankings were scored by JEV).

### Answer quality (blind judge, paired per question)

Primary metric: correctness + completeness (0–4), judged against the reference
answer. Groundedness and citation correctness are reported but favour answers
that decline (the closed-book arm scores 1.91 / 1.86 on them by saying it does
not know).

| Pairing | n | Mean C+C (EL or first arm) | Mean C+C (baseline) | Δ | 95% CI (paired bootstrap) | W / T / L |
|---|---|---|---|---|---|---|
| EL boost off r1 vs A (files) | 41 | 1.98 | 2.59 | −0.61 | [−1.12, −0.10] | 5 / 24 / 12 |
| EL boost on r1 vs A (files) | 42 | 1.90 | 2.67 | −0.76 | [−1.29, −0.26] | 4 / 24 / 14 |
| EL boost off r1 vs B (RAG) | 42 | 1.90 | 2.21 | −0.31 | [−0.79, +0.12] | 6 / 27 / 9 |
| EL boost off r2 vs B (RAG) | 44 | 1.86 | 2.18 | −0.32 | [−0.77, +0.11] | 6 / 29 / 9 |
| EL boost on r1 vs B (RAG) | 42 | 1.93 | 2.14 | −0.21 | [−0.64, +0.17] | 6 / 28 / 8 |
| EL boost on r2 vs B (RAG) | 43 | 1.93 | 2.16 | −0.23 | [−0.65, +0.16] | 5 / 30 / 8 |
| Closed book vs B (RAG) | 44 | 0.00 | 2.11 | −2.11 | [−2.66, −1.59] | 0 / 16 / 28 |

Per-metric means (first arm / baseline), 0–2 each:

| Pairing | Correctness | Completeness | Groundedness | Citation correctness |
|---|---|---|---|---|
| EL off r1 vs A | 1.07 / 1.37 | 0.90 / 1.22 | 1.95 / 1.85 | 1.98 / 1.90 |
| EL on r1 vs A | 1.02 / 1.38 | 0.88 / 1.29 | 1.86 / 1.86 | 1.88 / 1.86 |
| EL off r1 vs B | 1.07 / 1.19 | 0.83 / 1.02 | 1.93 / 1.76 | 1.93 / 1.76 |
| EL off r2 vs B | 1.02 / 1.18 | 0.84 / 1.00 | 1.86 / 1.80 | 1.86 / 1.73 |
| EL on r1 vs B | 1.05 / 1.14 | 0.88 / 1.00 | 1.90 / 1.79 | 1.90 / 1.71 |
| EL on r2 vs B | 1.05 / 1.16 | 0.88 / 1.00 | 1.93 / 1.77 | 1.93 / 1.74 |
| Closed book vs B | 0.00 / 1.11 | 0.00 / 1.00 | 1.91 / 1.82 | 1.86 / 1.75 |

On answerable questions, the answers said the evidence was insufficient or did
not contain the fact (regex heuristic, indicative only) in 16/43 (EL off r1),
15/43 (EL on r1), 21/42 (EL lexical), 5/43 (A) and 12/44 (B).

### Abstention (5 unanswerable questions)

Every arm abstained on all 5 (40/40 judgements). The questions asked for facts
absent from the repository (support phone number, SOC 2 auditor, customer count,
Kubernetes version, education discount). A 5-question sample cannot distinguish
the arms.

### Tokens (provider-reported answer input tokens, paired per question)

| Comparison | Questions | EL total | Baseline total | Saving | Median per-question saving |
|---|---|---|---|---|---|
| EL off r1 vs A | 46 | 63,491 | 540,609 | 88.3% | 86.2% |
| EL on r1 vs A | 47 | 53,833 | 553,408 | 90.3% | 86.5% |
| EL off r2 vs A | 48 | 54,335 | 569,028 | 90.5% | 88.1% |
| EL on r2 vs A | 47 | 58,524 | 553,408 | 89.4% | 86.9% |
| EL lexical vs A | 46 | 68,947 | 548,295 | 87.4% | 84.8% |
| EL off r1 vs B | 47 | 66,446 | 74,633 | 11.0% | 14.8% |
| EL on r1 vs B | 47 | 53,061 | 70,524 | 24.8% | 14.8% |
| EL off r2 vs B | 49 | 56,474 | 77,420 | 27.1% | 17.6% |
| EL on r2 vs B | 48 | 61,214 | 76,060 | 19.5% | 17.4% |
| EL lexical vs B | 46 | 67,638 | 69,265 | 2.3% | 5.2% |

Baseline A averaged 11,855 input tokens per answer (median 9,180; many files are
shorter than the 24,000-token cap). EL and B use the same 1,200-token evidence
budget, so their difference comes from how much of the budget each fills.

### Diagnosis: why EL recall is low

Measured offline on the same corpus and questions (no provider calls):

| Measure | Result |
|---|---|
| JEV candidate batch size today | median **3**, min 2, max 6 chunks |
| Gold reachable by JEV today (in its batch) | 10/45, exactly the lexical arm's recall |
| Gold in the lexical top 50 (if the batch had its own budget) | 26/45 |
| Gold in the lexical top 200 | 33/45 |

`_retrieval_response` builds the JEV batch with `top_k=50` but with the
response's own token/character budget (1,200 tokens, 6,000 characters), so JEV
only re-orders the 2–6 chunks lexical scoring already selected. It therefore
cannot raise recall, which is why the JEV and lexical arms have identical recall.
The second weakness is the lexical first stage itself: plain-language questions
reach the gold in 1/22, against 2/22 for BM25 chunks and 6/22 for whole files.

## Spend

| Item | Cost |
|---|---|
| DeepSeek answers (400 calls) | $1.833 at the peak rate used by the hard cap; the run was off-peak, so the expected charge is about half ($0.92) |
| OpenAI judge (298 calls) | $4.650 |
| OpenAI abstention checks (40 calls) | $0.033 |
| JEV (200 rankings) | $0.300 charged to the cap (conservative); ≤ $0.25 at $0.042 per million input tokens |
| **Budget ledger total** | **$6.82** (cap $25; not reached). The addendum below added $0.59 (total $7.41) |

Prices were re-checked on the providers' pages at 11:29 UTC on the day of the run.
Both models were listed as available in the accounts.

## Limits

- **Author-written questions.** The questions and gold labels were written by the
  operator, and many target code and documentation written in the same week.
  They are not blind. The owner reviewed the first 10 drafts and the final list.
- **One repository** (this one), in English. The results don't carry over to other corpora.
- **Strict recall metric.** A fact counts only when its exact string (after
  whitespace normalisation) is in the selected evidence. Evidence that conveys the
  same fact in other words counts as a miss. This affects all arms equally, but it
  favours long contexts such as baseline A.
- **LLM judge.** One judge model with its own biases. The reference answers were
  written by the operator from the gold sources. Groundedness and citation scores
  reward answers that decline.
- **11 of 400 answers incomplete.** The answer model hit its 800-token output limit
  on all 4 attempts. By arm: `el_off_lex` 3, `base_files` 2, `el_off_jev_r1` 2,
  `el_on_jev_r1` 2, `base_rag` 1, `el_on_jev_r2` 1. By question: M05 ×4, M02 ×3,
  C01, C05, C09, P11. Pairings involving them were not judged, so n is 41–44 per
  pairing instead of 45.
- **JEV variance.** Two runs per JEV arm; selections differed on 6–7 of 50
  questions without changing recall.
- **Unanswerable set of 5** is too small to compare abstention between arms.
- **Snapshot vs production.** The corpus is `b71856f` with 3 files excluded by the
  secret scan; production indexes the live repository. Relationships were not
  used (none exist in the production workspace).
- **Insufficient-evidence counts** come from a regex and are indicative only.
- **Provider cost and latency** of production requests are not measured here.

## Reproduction and data handling

The runner, the analysis script, the frozen question set (with reference answers),
the snapshot manifest, the secret-scan result and all raw provider outputs are kept
**outside the repository**, in the operator's Codespace (`~/.el-ops/gate/`). They
are not committed because the raw outputs and the selected evidence contain
repository text beyond short excerpts. This report contains only aggregate numbers,
per-question pass/fail marks, the questions and the short gold strings.

## Proposed follow-ups (not decided)

1. Give the JEV candidate batch its own budget (e.g. 50 chunks), separate from the
   response budget, so JEV can re-rank beyond the lexical top 3. Re-measure with
   this gate, including latency and JEV cost.
2. Replace or complement the lexical first stage with BM25 (idf-weighted), which
   beat the current cosine scoring on recall (14 vs 10).
3. Exclude or down-rank archived snapshots such as `evidence/closure-2026-11-01/`,
   which take result slots from current documents (already queued).
4. Re-run the 11 incomplete answers with a larger output limit: done as a separate
   addendum (below); conclusions unchanged.
5. The identifier boost: +2 recall on code-identifier questions with no measurable
   change in answer quality on this set. Enabling it is the owner's decision.

## Addendum — rerun of the 11 incomplete answers (not part of the frozen protocol)

**This section does not change any table above.** Decided after the main run:
only the 11 answer calls that came back incomplete (the 800-token output limit was
reached on all 4 attempts) were rerun with a 2,000-token output limit. Same
prompts, models, evidence and judge instructions, and the blind order each of
these comparisons would have had in the main run. Then only the judge comparisons
they unlocked were run. The main raw results were not modified (verified by hash).

| Rerun answer | Result with 2,000 output tokens |
|---|---|
| C01 `base_files` | complete |
| C05 `el_off_jev_r1` | complete |
| C09 `base_rag` | complete |
| M02 `base_files`, `el_off_lex`, `el_on_jev_r1` | complete |
| M05 `el_off_jev_r1`, `el_on_jev_r1`, `el_on_jev_r2` | complete |
| M05 `el_off_lex` | **still incomplete** (2,000 tokens reached on all 4 attempts) |
| P11 `el_off_lex` | complete |

Newly judged comparisons (correctness + completeness, first arm vs baseline):

| Pairing | New comparisons |
|---|---|
| EL off r1 vs A (files) | C01 0–2, C05 1–4, M02 3–3, M05 1–4 |
| EL on r1 vs A (files) | C01 4–2, M02 3–3, M05 1–4 |
| EL off r1 vs B (RAG) | C05 2–0, C09 3–0, M05 0–3 |
| EL off r2 vs B (RAG) | C09 4–0 |
| EL on r1 vs B (RAG) | C09 4–0, M02 3–3, M05 0–3 |
| EL on r2 vs B (RAG) | C09 3–0, M05 0–3 |
| Closed book vs B (RAG) | C09 0–2 |

Main comparisons plus these (every pairing now n = 45), paired bootstrap 95% CI.
The EL lexical arm has no judged pairing, so its recovered answers do not appear here:

| Pairing | Mean C+C | Δ | 95% CI | W / T / L |
|---|---|---|---|---|
| EL off r1 vs A | 1.91 vs 2.64 | −0.73 | [−1.20, −0.24] | 5 / 25 / 15 |
| EL on r1 vs A | 1.96 vs 2.69 | −0.73 | [−1.27, −0.24] | 5 / 25 / 15 |
| EL off r1 vs B | 1.89 vs 2.13 | −0.24 | [−0.73, +0.22] | 8 / 27 / 10 |
| EL off r2 vs B | 1.91 vs 2.13 | −0.22 | [−0.73, +0.22] | 7 / 29 / 9 |
| EL on r1 vs B | 1.96 vs 2.13 | −0.18 | [−0.62, +0.27] | 7 / 29 / 9 |
| EL on r2 vs B | 1.91 vs 2.13 | −0.22 | [−0.67, +0.18] | 6 / 30 / 9 |
| Closed book vs B | 0.00 vs 2.11 | −2.11 | [−2.62, −1.62] | 0 / 16 / 29 |

The conclusions do not change: EL stays below baseline A (the interval excludes zero) and
statistically tied with baseline B. Retrieval, recall and answer input tokens do
not depend on the output limit, so those results are unchanged.

Addendum spend: $0.594 (11 answer calls at the peak DeepSeek rate, plus 17 judge calls);
cost ledger total for main run plus addendum: **$7.41** of the $25 cap.

## Appendix A — per-question recall

✓ = gold facts present in the selected evidence; · = not present.

| ID | Style | EL off r1 | EL off r2 | EL on r1 | EL on r2 | EL lex | A files | B RAG |
|---|---|---|---|---|---|---|---|---|
| C01 | technical | · | · | ✓ | ✓ | · | ✓ | ✓ |
| C02 | technical | · | · | ✓ | ✓ | · | ✓ | ✓ |
| C03 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| C04 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| C05 | technical | · | · | · | · | · | ✓ | · |
| C06 | technical | · | · | · | · | · | ✓ | · |
| C07 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| C08 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| C09 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | · | · |
| C10 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| P01 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| P02 | technical | · | · | · | · | · | ✓ | ✓ |
| P03 | plain | · | · | · | · | · | · | · |
| P04 | plain | · | · | · | · | · | · | · |
| P05 | plain | · | · | · | · | · | · | · |
| P06 | plain | · | · | · | · | · | · | · |
| P07 | plain | · | · | · | · | · | ✓ | ✓ |
| P08 | plain | · | · | · | · | · | ✓ | · |
| P09 | plain | · | · | · | · | · | · | · |
| P10 | technical | · | · | · | · | · | ✓ | ✓ |
| P11 | plain | · | · | · | · | · | · | · |
| P12 | plain | · | · | · | · | · | · | · |
| P13 | plain | · | · | · | · | · | ✓ | · |
| O01 | technical | · | · | · | · | · | · | · |
| O02 | plain | · | · | · | · | · | · | · |
| O03 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| O04 | plain | · | · | · | · | · | · | · |
| O05 | plain | · | · | · | · | · | · | · |
| O06 | plain | · | · | · | · | · | · | · |
| O07 | plain | · | · | · | · | · | ✓ | ✓ |
| O08 | technical | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| O09 | plain | ✓ | ✓ | ✓ | ✓ | ✓ | · | · |
| O10 | plain | · | · | · | · | · | · | · |
| O11 | plain | · | · | · | · | · | · | · |
| O12 | technical | · | · | · | · | · | ✓ | · |
| O13 | plain | · | · | · | · | · | ✓ | · |
| M01 | technical | · | · | · | · | · | · | · |
| M02 | technical | · | · | · | · | · | · | · |
| M03 | technical | · | · | · | · | · | · | · |
| M04 | plain | · | · | · | · | · | · | · |
| M05 | technical | · | · | · | · | · | ✓ | ✓ |
| M06 | plain | · | · | · | · | · | ✓ | · |
| M07 | technical | · | · | · | · | · | · | · |
| M08 | plain | · | · | · | · | · | · | · |
| M09 | technical | · | · | · | · | · | · | · |

## Appendix B — frozen questions (v2)

| ID | Type | Style | Question | Gold (AND/OR) |
|---|---|---|---|---|
| C01 | code-identifier | technical | Where is the list_not_indexed_connector_files route defined, and which errors does it turn into 404, 403 and 503 responses? | `async def list_not_indexed_connector_files` |
| C02 | code-identifier | technical | What does split_parts return for a text that fits in a single evidence record? | `return [(0, length)]` |
| C03 | code-identifier | technical | Which rows does delete_stale_connector_evidence delete, and which role may call it? | `not (connector_external_id = any(p_current_external_ids))` |
| C04 | code-identifier | technical | How does _read_retrieval_candidates avoid running the full-text search when the workspace fits in the window? | `search_limit=0` |
| C05 | code-identifier | technical | How does authorize_personal_api_key_read enforce the per-key limit of 60 reads per minute? | `if current_count >= 60 then` |
| C06 | code-identifier | technical | With which credentials is consume_user_usage called, and on which path? | `public.consume_user_usage`` with the signed-in user's JWT` |
| C07 | code-identifier | technical | What does parse_part_label return for a source label that has no part suffix? | `def parse_part_label(source_label: str \| None) -> PartLocation \| Non` |
| C08 | code-identifier | technical | How does describe_cause keep URLs and identifiers out of the service_unavailable log line? | `It never reads exception messages` |
| C09 | code-identifier | technical | When does _with_workspace_search skip the full-text search on the signed-in path? | `if len(records) < RETRIEVAL_CANDIDATE_LIMIT:` |
| C10 | code-identifier | technical | What multiplier does identifier_boost return for a Markdown documentation file? | `if not identifiers or not is_code_path(path):` |
| P01 | product | technical | What is the largest file the GitHub connector will fetch, and what happens to a file above that size? | `2 MiB` AND `too_large` |
| P02 | product | technical | What are the default per-user retrieval caps, and what does a user get when the daily JEV budget is used up? | `DEFAULT_RETRIEVAL_PER_MINUTE = 30` / `EVIDENCE_USER_RETRIEVAL_PER_MINUTE` \| `30`` AND `jev_budget_exceeded` |
| P03 | product | plain | If I connect my GitHub account, can Evidence Layer change or push anything to my repositories? | `**Contents: read**` OR `No files are         written back to the provider` |
| P04 | product | plain | Which kinds of files from my repository can be searched? | `every allow-listed source extension` OR `TEXT_MIME_TYPES = {` |
| P05 | product | plain | When I prepare context for a task in the web app, what does Evidence Layer keep a record of, and does it keep what I typed? | `It does **not** store task text` OR `not stored: task text` |
| P06 | product | plain | If the smart ranking service has a problem in the middle of my request, do I still get results, and how are they ordered? | `discards every JEV score and recomputes the complete` |
| P07 | product | plain | My repository has some very long source files. Are they searched completely, or are they cut off? | `Large files are indexed whole, in parts` OR `PART_MAX_CHARS = 60_000` |
| P08 | product | plain | Where can I see which of my files were skipped, and can other people see that list? | `Only you can see this list` OR `read with the owner's session under RLS` |
| P09 | product | plain | How many pieces of evidence can I ask for at most in one search? | `top_k: int = Field(default=5, ge=1, le=20)` |
| P10 | product | technical | What is the largest candidate_limit an API-key retrieval accepts? | `capped at, 1000 records` |
| P11 | product | plain | Does Evidence Layer send pieces of my documents to an outside company when it ranks results? | `up to 50 budget-bounded candidate snippets` OR `System One scoring request` |
| P12 | product | plain | What happens when I reach my usage limit, and how do I know when I can try again? | `user_rate_limited` AND `Retry-After` |
| P13 | product | plain | Does the search understand questions that name a specific function from my code? | `EVIDENCE_IDENTIFIER_BOOST_ENABLED` |
| O01 | ops | technical | How long does a recovery attestation stay valid before a production migration can be applied? | `RECOVERY_MAX_AGE_SECONDS = 48 * 3600` OR `expires 48 hours after upload` |
| O02 | ops | plain | Can a code change merged into the main branch change the production database by itself? | `A push to main plans but never applies` |
| O03 | ops | technical | After a restore drill, what does the deletion reconciliation verifier prove, and what does it explicitly not prove? | `do not resurrect terminal` |
| O04 | ops | plain | How long are database backups kept after they are taken for a change? | `Keep the B2 object for at least 14 days` |
| O05 | ops | plain | Where are copies of the production database stored? | `\| Provider \| Backblaze B2 \|` |
| O06 | ops | plain | How often are my GitHub repositories checked for changes in the background? | `CONNECTOR_SYNC_INTERVAL_SECONDS", 900` |
| O07 | ops | plain | What happens to background syncing for a user whose account is being deleted? | `skipped_account_unavailable` |
| O08 | ops | technical | Which dump format and schemas are used for the production restore drill? | `PostgreSQL custom dump` |
| O09 | ops | plain | Can the production database be restored to any point in time? | `PITR remains disabled` |
| O10 | ops | plain | If a safety check against the backup system fails a single time because of a network blip, does my request fail? | `retries exactly once` |
| O11 | ops | plain | When a request fails because something in the backend is down, what is recorded about why? | `"event": "service_unavailable"` |
| O12 | ops | technical | How is a production migration applied, and what confirmation is required? | `Apply only on a manual dispatch` |
| O13 | ops | plain | If the system that counts my usage is down, can I still search? | `usage_counter_unavailable` |
| M01 | multi-file | technical | How does the database verify that an API-key evidence read really comes from the evidence service, and can a captured request be replayed? | `personal_api_key_gateway_nonces` AND `x-evidence-api-key-gateway-proof` |
| M02 | multi-file | technical | How does a large file travel from GitHub sync into search results, and how does retrieval know which part of the file a chunk came from? | `def part_external_id` AND `parse_part_label(record.source_label)` |
| M03 | multi-file | technical | How is workspace isolation enforced for connector evidence written by the background worker? | `connector evidence requires a personal workspace owner` |
| M04 | multi-file | plain | If I disconnect my GitHub or delete my account, what happens to the documents that were synced? | `references public.connector_connections (connection_id) on delete casc` OR `disconnecting the source removes its parts` |
| M05 | multi-file | technical | Which limits does one API-key retrieval count against: per key and per user? | `counts **once each** against the per-key 60/minute limit` AND `1 to 3 per retrieval` AND `per-user caps count **one** retrieval` |
| M06 | multi-file | plain | In a very large workspace, could an old document be missed by search, and what was done about it? | `Whole-workspace prefilter` |
| M07 | multi-file | technical | How does the web app get the list of files not indexed from the evidence service? | `proxyListNotIndexed` AND `/v1/connectors/{connection_id}/not-indexed` |
| M08 | multi-file | plain | If a long document is stored in overlapping pieces, could my results contain duplicated passages? | `never returns the same file range twice` OR `selected_file_ranges` |
| M09 | multi-file | technical | How does the migration guard decide that a recent recovery point exists before an apply? | `def external_recovery_is_recent` |
| U01 | unanswerable | plain | What is the phone number for Evidence Layer customer support? | abstain |
| U02 | unanswerable | plain | Which audit firm issued Evidence Layer's SOC 2 report, and in which year? | abstain |
| U03 | unanswerable | plain | How many paying customers did Evidence Layer have at the end of October 2026? | abstain |
| U04 | unanswerable | plain | Which Kubernetes version does the production cluster run? | abstain |
| U05 | unanswerable | plain | What discount does Evidence Layer give to universities and students? | abstain |
