# Evidence Layer vs baseline — multi-provider A/B

Date: 2026-11-14

Mode: read-only production retrieve + provider generation. No customer data, flags, migrations, or worker settings changed. JEV was not used in these calls.

## Deployment and corpus

| Item | Value |
|---|---|
| Evidence Service | `https://evidence-layer-production-c4b5.up.railway.app` |
| Deployment under test | `47204243-b254-4d7a-8579-159ff179b6ea` — `SUCCESS` |
| Source commit | `1209e9c` |
| Workspace | `f67f0f38-bb05-4ec3-88a3-50b7005b0172` (personal) |
| Bridged GitHub evidence | 49 documents, 269 chunks |
| Selection | Lexical, `retrieve_time_v1`, relevance floor 35% |
| Questions | 15-question protocol in `tests/fixtures/retrieval_harness/ab_questions.jsonl` |

Arm A used 8 documents / **78,595 chars** from the same priority order as the
2026-11-13 report. Arm B used production retrieve with `top_k=8`,
`max_tokens=3500`, `max_chars=14000`, `candidate_limit=100`. Mean selected
context was **3,440.8 estimated tokens**; 7.0 chunks/question.

## Provider settings

The DeepSeek protocol is the same as the prior report. OpenAI and Anthropic
cannot accept temperature `0.2` with the currently pinned models; model-native
defaults were used and are recorded here.

| Provider | Model | Output cap | Temperature / controls |
|---|---|---:|---|
| DeepSeek | `deepseek-chat` | 400 | `0.2`; returned `deepseek-flash` |
| OpenAI | `gpt-6.1-sol` | 1,200 completion tokens | default temperature; `reasoning_effort=low` |
| Anthropic | `claude-haiku-5-5` | 400 | temperature omitted; thinking disabled |

OpenAI `gpt-6.1-sol` rejected `temperature=0.2`; Anthropic rejected
`temperature` as deprecated. These settings are therefore not identical across
providers, but each provider's A/B uses the same model and settings for both
arms.

## Token results

DeepSeek / OpenAI / Anthropic totals; positive reduction means fewer tokens with EL.

| # | DeepSeek A/B (reduction) | OpenAI A/B (reduction) | Anthropic A/B (reduction) |
|---:|---|---|---|
| 1 | 18,887 / 3,671 (80.6%) | 18,207 / 3,499 (80.8%) | 29,708 / 6,079 (79.5%) |
| 2 | 19,152 / 3,920 (79.5%) | 18,525 / 3,706 (80.0%) | 29,904 / 6,074 (79.7%) |
| 3 | 19,037 / 3,604 (81.1%) | 18,416 / 3,669 (80.1%) | 29,889 / 6,105 (79.6%) |
| 4 | 19,142 / 3,742 (80.5%) | 18,548 / 3,885 (79.1%) | 29,490 / 5,707 (80.6%) |
| 5 | 19,103 / 3,286 (82.8%) | 18,343 / 3,244 (82.3%) | 29,886 / 5,417 (81.9%) |
| 6 | 19,068 / 4,063 (78.7%) | 18,406 / 3,877 (78.9%) | 29,893 / 6,275 (79.0%) |
| 7 | 18,743 / 3,918 (79.1%) | 18,147 / 3,569 (80.3%) | 29,486 / 6,080 (79.4%) |
| 8 | 18,752 / 3,818 (79.6%) | 18,063 / 3,617 (80.0%) | 29,690 / 5,624 (81.1%) |
| 9 | 18,805 / 3,231 (82.8%) | 18,047 / 3,150 (82.5%) | 29,598 / 5,244 (82.3%) |
| 10 | 18,841 / 3,427 (81.8%) | 18,217 / 3,339 (81.7%) | 29,492 / 5,693 (80.7%) |
| 11 | 18,790 / 3,595 (80.9%) | 18,075 / 3,459 (80.9%) | 29,492 / 5,817 (80.3%) |
| 12 | 19,104 / 3,158 (83.5%) | 18,312 / 3,040 (83.4%) | 29,889 / 5,346 (82.1%) |
| 13 | 19,000 / 3,704 (80.5%) | 18,327 / 3,662 (80.0%) | 29,888 / 5,849 (80.4%) |
| 14 | 18,748 / 3,456 (81.6%) | 18,048 / 3,337 (81.5%) | 29,494 / 5,605 (81.0%) |
| 15 | 19,142 / 3,803 (80.1%) | 18,565 / 3,791 (79.6%) | 29,884 / 5,976 (80.0%) |

### Aggregates

| Provider | Mean A | Mean B | Mean reduction | All 15 lower? |
|---|---:|---:|---:|---|
| DeepSeek | 18,954.3 | 3,626.4 | **80.9%** | yes |
| OpenAI | 18,283.1 | 3,522.9 | **80.7%** | yes |
| Anthropic | 29,712.2 | 5,792.7 | **80.5%** | yes |

The token reduction is stable across all three providers: about **80.5–80.9%**.

## Quality

Primary signal: explicit `insufficient context` responses, plus manual review of
Q1, Q2, and Q13. The cross-model automated judge was not used as the primary
signal because it favored the baseline on Q1 despite equivalent answers.

| Provider | B wins | Ties | Losses | Insufficient A → B |
|---|---:|---:|---:|---:|
| DeepSeek | 3 | 11 | 1 | 3 → 1 |
| OpenAI | 3 | 11 | 1 | 4 → 2 |
| Anthropic | 4 | 11 | 0 | 4 → 0 |

Wins are questions where A returned insufficient context and B answered.
Losses are the reverse. A tie includes both sufficient or both insufficient.
This is a sufficiency comparison, not an independent factuality or preference
study.

### Q1 — product definition

All three providers answered sufficiently in both arms; **tie for each**.
B selected `README.md`, `docs/PRD.md`, the adaptive-selection ADR,
`docs/ARCHITECTURE.md`, and further product/architecture chunks. The README
ranking regression is fixed.

### Q2 — architecture

All three providers answered sufficiently in both arms; **tie for each**. B
selected `apps/web/README.md`, `docs/ARCHITECTURE.md`, and the Evidence Service
README, and included the web/service/Supabase/Railway split. Some source text
still describes older service boundaries, so this remains a source-freshness
risk rather than a retrieval miss.

### Q13 — deletion worker

All three providers answered sufficiently in both arms; **tie for each**. B
selected the current implementation audit and deletion closure documents and
covered the one-replica advisory lock, worker image, health-check cron,
Storage/Auth cleanup, and durability limits.

### Other differences

- **DeepSeek loss:** Q9 ROI/cost calculator — A answered; B returned
  `insufficient context`.
- **OpenAI loss:** Q12 cross-tenant/ownerless-write controls — A answered; B
  returned `insufficient context`.
- **Anthropic:** no loss; B removed four `insufficient context` answers.
- **Wins:** Q7/Q8/Q14 for DeepSeek, Q8/Q11/Q14 for OpenAI, and
  Q7/Q10/Q11/Q14 for Anthropic.

## Limits

- Baseline is bounded at 80k chars; this is not a full-corpus baseline.
- OpenAI and Anthropic use model-native generation settings because their
  pinned models reject temperature `0.2`.
- Output caps differ (DeepSeek/Anthropic 400; OpenAI 1,200 completion tokens).
- The automated judge was inconsistent with equivalent Q1 answers; quality
  labels are manual sufficiency judgments, not independent human preference.
- Fifteen questions remain a small evaluation set; this is not the sealed
  confirmatory set.
- JEV was not used; provider cost was not computed from invoice prices.
- The retrieve response used `chars_div_4` token estimates; provider usage is
  authoritative for generation tokens.

## Conclusion

Broadening the A/B to OpenAI and Anthropic works. The production retrieve path
preserved **80.5–80.9% token reduction** with all 15 questions lower for every
provider and no broad quality collapse. Anthropic had the strongest sufficiency
result (4 wins, 0 losses); DeepSeek and OpenAI each had one remaining
insufficiency regression.

This is a retrospective internal result, not a confirmatory human study. The
next validation step remains the blinded Phase 2 comparison on the existing
answer pairs, followed by a sealed set for public claims.
