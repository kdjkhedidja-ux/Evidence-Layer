# Phase 7 — Traditional RAG vs Evidence Layer, Anthropic (roster v1)

Status: **EXECUTION PASS** (live, 2026-11-06). 3 models × 40 cases × 2 arms = 240 answers, 0 failed answers, 120/120 blinded paired judgments. No production policy change: lexical remains the default and JEV remains optional.

## Method

- Fixed Real-World v1 cases (40) and frozen passages (51), verified by hash and candidate match.
- **Traditional RAG (A):** lexical ranking, top-5 passages passed as context.
- **Evidence Layer (B):** JEV ranking from the saved Phase 6 scores (all 200 candidate hashes re-verified), top-3 passages passed as context.
- Same instructions, question, citation-label format, and answer model within each pair.
- Answer profile: Anthropic Messages, `anthropic-version 2026-10-21`, adaptive thinking, `output_config.effort=medium`, `max_tokens=8192`. This differs from Phase 7's 800 total output cap for OpenAI/DeepSeek and is recorded as a difference.
- Judge: the existing Phase 7 blinded paired rubric (OpenAI `gpt-6.1-sol`), scores 0–2 on correctness, completeness, groundedness, citation correctness. Judge cost is evaluation overhead, excluded from serving cost.
- No live JEV calls: saved Phase 6 scores were reused, so JEV USD is an **estimate** from measured Phase 6 tokens.

Artifacts: `reports/phase7_anthropic_end_to_end.json`, `reports/phase7_anthropic_end_to_end_manifest.json`, per-model partials. Answers and passage bodies are **not** stored: only SHA-256 hashes, counts, scores, and metadata.

## Results per model

T = Traditional RAG, E = Evidence Layer. Tokens are mean per case. USD is per 40 cases.

### claude-haiku-5-5

| Metric | T | E | E − T |
|---|---:|---:|---:|
| Input tokens / case | 648.05 | 465.23 | −182.83 |
| Output tokens / case | 258.10 | 245.20 | −12.90 |
| Total tokens / case | 906.15 | 710.43 | −195.73 |
| Correctness (0–2) | 1.975 | 2.000 | +0.025 |
| Completeness | 1.950 | 1.900 | −0.050 |
| Groundedness | 1.950 | 1.975 | +0.025 |
| Citation correctness | 1.925 | 2.000 | +0.075 |
| Gold passage recall | 1.000 | 0.979 | −0.021 |
| Citation reference validity | 1.000 | 1.000 | 0.000 |
| Citation relevance precision | 0.804 | 0.869 | +0.065 |
| LLM USD (measured) | 0.007754 | 0.006765 | −0.000989 |
| JEV USD | 0 (`not_applicable`) | 0.003200 (`estimate`) | +0.003200 |
| System USD | 0.007754 (`measured`) | 0.009965 (`measured LLM + estimated JEV`) | **+0.002210** |

### claude-sonnet-5-5

| Metric | T | E | E − T |
|---|---:|---:|---:|
| Input tokens / case | 648.05 | 465.23 | −182.83 |
| Output tokens / case | 174.70 | 166.45 | −8.25 |
| Total tokens / case | 822.75 | 631.68 | −191.08 |
| Correctness | 2.000 | 1.975 | −0.025 |
| Completeness | 1.950 | 1.925 | −0.025 |
| Groundedness | 2.000 | 1.975 | −0.025 |
| Citation correctness | 2.000 | 2.000 | 0.000 |
| Gold passage recall | 1.000 | 0.979 | −0.021 |
| Citation reference validity | 1.000 | 1.000 | 0.000 |
| Citation relevance precision | 0.812 | 0.865 | +0.053 |
| LLM USD (measured) | 0.121724 | 0.103798 | −0.017926 |
| JEV USD | 0 (`not_applicable`) | 0.003200 (`estimate`) | +0.003200 |
| System USD | 0.121724 (`measured`) | 0.106998 (`measured LLM + estimated JEV`) | **−0.014726** |

### claude-opus-5-5

| Metric | T | E | E − T |
|---|---:|---:|---:|
| Input tokens / case | 648.05 | 465.23 | −182.83 |
| Output tokens / case | 267.70 | 257.78 | −9.93 |
| Total tokens / case | 915.75 | 723.00 | −192.75 |
| Correctness | 2.000 | 2.000 | 0.000 |
| Completeness | 1.950 | 1.925 | −0.025 |
| Groundedness | 2.000 | 2.000 | 0.000 |
| Citation correctness | 2.000 | 2.000 | 0.000 |
| Gold passage recall | 1.000 | 0.979 | −0.021 |
| Citation reference validity | 1.000 | 1.000 | 0.000 |
| Citation relevance precision | 0.818 | 0.843 | +0.025 |
| LLM USD (measured) | 0.317848 | 0.280656 | −0.037192 |
| JEV USD | 0 (`not_applicable`) | 0.003200 (`estimate`) | +0.003200 |
| System USD | 0.317848 (`measured`) | 0.283856 (`measured LLM + estimated JEV`) | **−0.033992** |

### Paired bootstrap (10,000 replicates, 95%, by case)

| Model | Δ total tokens | Δ correctness | Δ LLM USD |
|---|---|---|---|
| haiku | −195.73 [−227.58, −164.55] | +0.025 [0.000, 0.075] | −0.000025 [−0.000039, −0.000010] |
| sonnet | −191.08 [−203.60, −179.12] | −0.025 [−0.075, 0.000] | −0.000448 [−0.000553, −0.000346] |
| opus | −192.75 [−211.73, −174.35] | 0.000 [0.000, 0.000] | −0.000930 [−0.001243, −0.000616] |

Token and LLM-cost reductions exclude zero. Every quality-difference interval includes zero: **no quality change is established**.

## JEV cost basis

```text
estimated_jev_usd = (76,184 measured Phase 6 input tokens / 1,000,000) × $0.042 per million
                  = $0.003199728 for 200 scores = 40 cases × 5 candidates
```

- Price source: <https://docs.typesafe.ai/models> (Jev 1.14), retrieved 2026-11-06; output tokens free.
- Per case: 5 × 380.92 tokens = 1,904.6 tokens → $0.0000800.
- Status: `estimate`. `invoiced_usd: null`. No invoice was queried.
- **Allocation caveat:** the table above charges the full $0.0032 to each model's Evidence Layer arm (dedicated scoring). JEV scores depend only on (case, candidate), so one scoring pass can be shared. Shared per model = $0.001066576; under that assumption the Haiku system delta becomes +$0.0000776 instead of +$0.0022104, and Sonnet/Opus remain lower.
- Phase 7 made no new JEV calls here either; scores were reused.

## Verdict

Evidence Layer cut total tokens by about 191–196 per answer (21–22%) for all three Claude models, with intervals excluding zero, and cut measured LLM cost in every case. Quality was already near the rubric ceiling and shows no established change: every quality interval includes zero, gold-passage recall dips from 1.000 to 0.979 because three passages are passed instead of five, while citation relevance precision rises modestly at the point estimate. On the estimated system-cost basis, direction depends on model scale: Sonnet and Opus are cheaper after adding the JEV estimate (−$0.0147 and −$0.0340 per 40 cases), while Haiku is more expensive (+$0.0022) because its measured LLM cost is far smaller than the JEV charge. This is a cost-structure effect, not a quality effect.

## Limitations

- Relevance judgments and required facts are developer-authored; no independent human adjudication.
- The judge is an automated OpenAI model; it is not independent of the OpenAI provider, though it judged Anthropic answers here.
- The 8-case exposed split is not blind and must not serve as a future blind JEV test.
- JEV USD is a list-price estimate from measured Phase 6 tokens, not an invoice.
- Latency here is answer latency only. Live JEV adds about 715 ms per case (sum of five Phase 6 candidate latencies); it is not included in the measured latency columns.
- The isolated local Supabase secure-read path from Phase 7 was not re-run; frozen passage bytes were verified by hash and candidate match instead.
- Answers use the frozen Anthropic profile (adaptive thinking, effort medium, 8192 cap), not Phase 7's 800 total output cap. Cross-provider token comparisons are therefore indicative only.
- The live 800-visible-token boundary and 8192 cap were not exercised; answers were short.

## Execution status

| Step | Status |
|---|---|
| A — JEV cost estimate from verified artifact, documented with source and date, labelled `estimate` | **PASS** |
| B — Anthropic Traditional vs Evidence Layer, three models, 40 paired cases each | **PASS** |
