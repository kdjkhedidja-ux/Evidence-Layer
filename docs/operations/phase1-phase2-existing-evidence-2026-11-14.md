# Phase 1–2 existing evidence and comparison plan

Date: 2026-11-14
Decision: [`ADR 0011`](../adr/0011-merge-phase1-validation-into-phase2-comparison.md)
Status: retrospective evidence accepted for internal decisions; confirmatory review pending

## What we reuse

No new retrieval or provider run is required to start Phase 2.

| Experiment | Existing artifact | Use in Phase 2 |
|---|---|---|
| Real-World v1 | `docs/phase6_real_world_benchmark.md`; `reports/phase6_real_world_lexical.json` | fixed 40-case structure and lexical reference |
| JEV vs Lexical | `docs/phase6_real_world_jev_report.md`; `reports/phase6_real_world_jev.json` | descriptive retrieval deltas and paired intervals |
| Phase 7 OpenAI/DeepSeek | `docs/phase7_end_to_end_benchmark.md`; `reports/phase7_end_to_end_rag.json` | answer pairs, automated quality, token/cost/latency |
| Phase 7 Anthropic | `docs/phase7_anthropic_end_to_end.md` | cross-model answer-pair replication |
| Phase 7 Haiku | `docs/phase7_haiku_lexical_only.md` | lexical-only paired replication |
| Chunked DeepSeek A/B | `docs/operations/el-vs-baseline-deepseek-chunked-2026-11-13.md` | post-chunking token reduction and manual sufficiency |

## What these runs establish

- Retrieval can be paired case-by-case and scored with deterministic metrics.
- The chunked production retrieve reduced DeepSeek context tokens by **80.7%**
  on the 15-question check, with **3 wins / 11 ties / 1 loss** on manual
  sufficiency.
- Phase 7 reduced LLM tokens by about **24% (OpenAI)** and **22% (DeepSeek)**.
- Phase 7 did **not** establish a general answer-quality gain; automated judge
  deltas were small and intervals included zero.
- JEV-vs-Lexical descriptive results are promising but exploratory and JEV is
  not enabled.

## What these runs do not establish

- independent human preference;
- blind superiority of Evidence Layer;
- quality on a sealed, previously unseen set;
- a production cost claim that includes every component;
- JEV production readiness.

## Phase 2 execution

1. Freeze the existing artifact list and hashes read-only.
2. Build a blinded comparison pack from saved answer pairs:
   - hide route/provider labels;
   - randomize order per pair with a private seed;
   - retain the mapping in a coordinator-only key.
3. Collect human votes from at least two independent reviewers:
   `A`, `B`, `Tie`, `Both bad`.
4. Reconcile disagreements with a third reviewer or panel.
5. Report:
   - human preference counts and agreement;
   - token, cost, and latency deltas from saved usage;
   - automated quality as secondary context;
   - explicit limits.
6. Do not call it confirmatory. For confirmatory claims, prepare a new sealed
   set and preregister the comparison before revealing labels.

## Exit criteria

**Retrospective gate (allows Phase 2 and non-claim Phase 3 work):**

- existing artifacts audited and hashed;
- comparison pack built without new provider calls;
- at least two reviewers complete votes;
- report published as retrospective.

**Confirmatory gate (allows public superiority claims):**

- new sealed set not used during tuning;
- preregistered metrics and stopping rules;
- independent human votes and adjudication;
- paired uncertainty reported;
- no result described beyond what the sealed set supports.

## Current recommendation

Start Phase 2 immediately from existing pairs. Keep Phase 3 work limited to
non-claim architecture until the confirmatory gate closes. Do not use the
exposed Real-World v1 split as a blind test.
