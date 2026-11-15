# ADR 0011: Merge Phase 1 validation into Phase 2 comparison

Date: 2026-11-14
Status: Accepted for roadmap planning

## Context

Phase 1 requires independent validation of evidence-selection value. Phase 2
already requires a comparison experience with human votes: A, B, Tie, Both bad.
The project has several documented paired experiments, but repeating retrieval
and provider runs just to create a separate Phase 1 gate is redundant and does
not remove the main limitation: the datasets and judgments were produced by the
project, and the Real-World v1 held-out split is exposed.

The existing evidence is useful, but it is not a sealed confirmatory study.
The roadmap therefore needs two levels:

- **retrospective validation** for internal product decisions;
- **confirmatory validation** for public superiority claims.

## Decision

Merge Phase 1 and Phase 2 into one validation-and-comparison phase.

1. Existing documented experiments are the retrospective evidence base. They
   are audited and reused; they are not rerun by default.
2. Phase 2 becomes the validation surface: blinded side-by-side answer
   comparison with human votes A / B / Tie / Both bad.
3. The comparison pack hides route labels, model/provider labels, scores, and
   known outcomes. Reviewer order is randomized.
4. At least two independent reviewers vote on every pair. Disagreements use a
   third reviewer or panel, without averaging grades.
5. Cost, token, and latency fields are taken from the saved experiment usage;
   they are not re-estimated as if they were new runs.
6. A new, previously unseen sealed set is required before any confirmatory
   quality or superiority claim. The exposed Real-World v1 split is never used
   as that sealed set.
7. Phase 3 non-claim work may start after the retrospective audit. External
   claims and launch copy remain blocked until the confirmatory gate closes.

## Existing evidence base

| Artifact | What it supports | Limitation |
|---|---|---|
| `phase6_real_world_benchmark.md` (40 cases, 51 passages) | retrieval benchmark structure and deterministic lexical reference | developer-authored grades; exposed split |
| `phase6_real_world_jev_report.md` | JEV-vs-Lexical descriptive MAP/MRR/P@1 and paired intervals | exploratory; no independent blind review; JEV remains off |
| `phase7_end_to_end_benchmark.md` (OpenAI/DeepSeek, 40 pairs) | token, cost, latency and automated answer-quality deltas | automated judge, not human; near ceiling; no quality gain established |
| `phase7_anthropic_end_to_end.md` (3 models, 240 answers) | cross-model robustness of the paired protocol | automated judge; not independent human validation |
| `phase7_haiku_lexical_only.md` | lexical-only paired replication | automated judge; one model |
| `el-vs-baseline-deepseek-chunked-2026-11-13.md` (15 questions) | post-chunking token reduction and manual sufficiency | small question set; manual labels |

## Phase 2 comparison rules

- Build the comparison pack from existing saved answer pairs; do not make new
  provider calls merely to populate the UI.
- Hide all route and provider labels and randomize left/right order per pair.
- Record one vote per reviewer per pair: `A`, `B`, `Tie`, or `Both bad`.
- Preserve the mapping key outside the reviewer pack.
- Report vote counts, agreement, and paired uncertainty descriptively.
- A label of "Evidence Layer" must not appear in the reviewer-facing pack.
- Any automated judge score is context, not a replacement for human votes.

## Consequences

- Phase 1 is no longer a separate blocking gate for internal progress.
- Phase 2 can start immediately with existing experiments.
- The project may say "retrospectively validated internally", not
  "independently validated" or "proven superior".
- Phase 3 can begin on non-claim work; public claims await the sealed
  confirmatory comparison.
- The review protocol remains valid for confirmatory relevance judgments and
  for a new sealed set.

## Out of scope

New provider runs, JEV enablement, egress approval, billing, and rewriting
historical artifacts. Existing reports remain point-in-time records.
