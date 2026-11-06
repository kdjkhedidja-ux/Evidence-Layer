# Phase 6 Model and Pricing Review: 2026-11-05

Status: **CANDIDATE ROSTER V1 FROZEN (8 MODELS); OPERATIONAL MATRIX NOT FROZEN; NOT AUTHORIZED**. This review used public official documentation only. No provider model-list API or generation call was made.

Pricing details were refreshed against Anthropic's full official pricing page on 2026-11-06. That refresh added cache read/write prices and Haiku's prompt-length tiers; the candidate count and API IDs did not change.

## Sources reviewed

- Anthropic: [Models overview](https://platform.claude.com/docs/en/models/overview)
- OpenAI: [Models](https://platform.openai.com/api/docs/models) and [Pricing](https://platform.openai.com/api/docs/pricing)
- DeepSeek: [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) and [List Models](https://api-docs.deepseek.com/api/list-models)

## Candidate decision

Eight exact current API IDs are suitable as candidates:

- Anthropic: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`.
- OpenAI: `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`.
- DeepSeek: `deepseek-flash`, `deepseek-v4-pro`.

OpenAI `gpt-6-astra` is explicitly excluded. DeepSeek's official source exposes two current model names; retired legacy Flash names are aliases served by the current Flash model and do not create a valid third candidate. A ninth candidate is not required; adding a placeholder or retired alias would violate the matrix identity rule.

## Pricing decision

Candidate snapshots are recorded with source URLs, retrieval date, USD units, input/output/cache dimensions, and provider conditions in `docs/phase6-pricing-registry.md`. The 2026-11-06 Anthropic pricing review resolved Haiku's prompt-length tiers and cache-read/write rates. Effective dates were not stated on the cited pricing pages, so `effective_at` remains `unknown`. DeepSeek peak/off-peak prices remain separate conditions; a run must record its UTC schedule. OpenAI short-context standard prices are separate from long-context, Batch/Flex/Fast, regional, and cache-write prices.

No row is approved for test or production. Egress remains closed, prompt/settings remain pending, and no pricing row is used in a cost calculation yet.

## Closure gates

| Gate | Status |
|---|---|
| Official source and exact API ID recorded | PASS for 8 candidates |
| Candidate Roster v1 | FROZEN: 3 Anthropic + 3 OpenAI + 2 DeepSeek |
| Model version/settings frozen | PENDING |
| Pricing conditions fully normalized | PARTIAL: billing applicability is modeled; DeepSeek write-billing semantics and effective dates remain unknown |
| Pricing sources/date recorded | PASS for candidate snapshots |
| Pricing effective dates verified | PENDING: source pages do not state an effective date |
| Data-transfer/egress approval | PENDING |
| Provider credentials and model-list verification | NOT RUN |
| Answer-model evaluation authorization | NOT AUTHORIZED |
