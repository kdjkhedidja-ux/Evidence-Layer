# Phase 7 — Haiku, Evidence Layer + lexical only (no JEV)

Status: **PASS**. Live paired run on the fixed Real-World v1 set, 2026-11-06. No JEV call was made and no saved JEV score was read on this path.

## Design

- Model: `claude-haiku-5-5`, same for both arms.
- **A — Traditional RAG:** lexical ranking, top 5 passages passed as context.
- **B — Evidence Layer + lexical:** the same lexical scorer, with the Evidence Layer budget (top 3).
- Same instructions, question, citation format, and Anthropic profile (adaptive thinking, `output_factor.effort=medium`, `max_tokens=8192`) in both arms.
- Judge: the existing Phase 7 blinded paired rubric (OpenAI `gpt-6.1-sol`), 40 paired judgments, 0 failures.
- 80 answers, 0 errors, 0 retries.

This isolates the *selection and context-size* effect from the JEV effect: the scorer is identical across arms.

## Results (40 cases, per case)

| Measure | Traditional | EL + lexical | Δ |
|---|---:|---:|---:|
| Input tokens | 648.05 | 457.65 | −190.40 |
| Output tokens | 264.20 | 243.35 | −20.85 |
| **Total tokens** | **912.25** | **701.00** | **−211.25 (−23.16 %)** |
| Correctness / 2 | 2.000 | 1.975 | −0.025 (CI includes 0) |
| Completeness / 2 | 1.950 | 1.725 | **−0.225 (CI −0.40 ; −0.075)** |
| Groundedness / 2 | 1.975 | 2.000 | +0.025 (CI includes 0) |
| Citation correctness / 2 | 1.975 | 2.000 | +0.025 (CI includes 0) |
| Gold passage recall | 1.0000 | 0.8708 | −0.1292 |
| Citation relevance precision | 0.8414 | 0.8625 | +0.0211 |
| **LLM cost (measured)** | **$0.007876** | **$0.006698** | **−14.96 %** |
| JEV cost | n/a | **$0** (not used) | — |
| **System cost estimate** | **$0.007876** | **$0.006698** | **−$0.001178 (−14.96 %)** |
| Answer latency | 1,768 ms | 1,634 ms | −134 ms |

Paired bootstrap (10,000 replicates, 95 %): total tokens **−211.25 [−238.85 ; −185.07]**; LLM cost **−0.00003 [−0.00004 ; −0.00002]**; completeness **−0.225 [−0.40 ; −0.075]**.

## Effect on the Haiku system-cost regression

The earlier Anthropic run used cached JEV scores and charged an estimated JEV amount. For Haiku that produced:

- Traditional $0.007754 → EL + JEV estimate $0.009965, i.e. **+$0.002210**.

Without JEV, the same comparison becomes:

- Traditional $0.007876 → EL + lexical $0.006698, i.e. **−$0.001178**.

So **removing JEV flips the Haiku system-cost direction from a small increase to a small decrease**. It is not free: completeness falls by 0.225 (interval excludes zero) and gold-passage recall falls from 1.000 to 0.871, because only three passages are passed instead of five. Token and cost reductions are real, but this run does **not** establish better answers.

## What this does not prove

- Answer quality improvement: completeness regressed and other rubric intervals include zero.
- Generality: 40 developer-labelled cases; the exposed split is not blind.
- Production readiness: the Anthropic profile and judge are evaluation settings.
- JEV behaviour: this path makes no JEV call and must not be read as a JEV result.

## Artifacts

- `reports/phase7_haiku_lexical_e2e.json` — report.
- `reports/phase7_haiku_lexical_e2e_manifest.json` — manifest with input and result hashes.
- `reports/phase7_haiku_lexical.json` — sanitized per-attempt records (no answer text).
- Runner: `scripts/run_phase7_haiku_lexical_e2e.py`.
- Public pages: `/evaluation/haiku-lexical-report.html`, `/evaluation/phase7-report.html`.
