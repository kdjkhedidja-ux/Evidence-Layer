# Provider and JEV activation — 2026-11-14

Scope: operator-approved activation of the hosted JEV scorer and verification of
evaluation-provider credentials. This record does not authorize sending customer
workspace content to any provider beyond the existing request-level JEV consent
contract.

## JEV production activation

| Item | Value |
|---|---|
| Evidence Service deployment | `03061119-4888-4334-ad02-e2b3ec899a0b` — `SUCCESS` |
| Source commit | `f88092b012f5440255bf21675c148cfebc1e8731` |
| Server flag | `EVIDENCE_JEV_ENABLED=true` |
| Provider credential | `TYPESAFE_API_KEY` configured as a server secret; value not recorded |
| Request gate | JEV is primary when server flag is open; `use_jev=false` forces lexical fallback |
| Fallback | any JEV configuration, provider, response, or validation failure discards the whole JEV batch and recomputes Lexical |

### Synthetic production smoke

A disposable smoke workspace received one synthetic password-policy record. The
task-context endpoint was called with `use_jev=true`, then the record was deleted.

| Check | Result |
|---|---|
| HTTP status | `200` |
| Retrieval status | `ok` |
| Selected evidence | `1` synthetic record |
| Selected tokens | `22` |
| Scoring provider | **`jev`** |
| Fallback reason | `null` |
| JEV score | `0.89` |
| Cleanup | evidence deleted; remaining records `0` |

The synthetic TypeSafe pre-check also returned scores `[0.96, 0.03]` for a
relevant/irrelevant pair. No customer or Real-World v1 content was sent.

## Evaluation provider credential check

These providers are only called by the explicit Phase 7 evaluation scripts;
the hosted Evidence Service does not generate model answers.

| Provider | Credential check | Generation check | State |
|---|---|---|---|
| DeepSeek | `/models` `200`, 2 models | `deepseek-chat` `200`, returned `deepseek-flash`, 9 tokens | **ACTIVE for evaluation** |
| OpenAI | `/models` `200`, 98 models | `gpt-6.1-sol` `200`, returned `gpt-6.1-sol`, 13 total tokens | **ACTIVE for evaluation** |
| Anthropic | `/models` `200`, 15 models after key rotation | `claude-haiku-5-5` `200`, returned `claude-haiku-5-5`, 4 output tokens | **ACTIVE for evaluation** |

No new full benchmark run was started. Phase 2 comparison remains based on the
existing documented answer pairs.

## Rollback

1. Set `EVIDENCE_JEV_ENABLED=false` on the Evidence Service and redeploy.
2. Optionally remove `TYPESAFE_API_KEY` from the Railway service.
3. Confirm retrieve responses fall back to Lexical with `scoring_provider` =
   `lexical_stopword_filtered` and no JEV call.

JEV is fail-closed: disabling the flag does not require a code rollback.

## Limits

- The smoke used synthetic text only.
- Real-World v1 egress for JEV remains a separate data-transfer decision.
- Provider-side JEV usage/cost is not returned by the current API contract.
- OpenAI generation requires `max_completion_tokens` for the pinned `gpt-6.1-sol` model; run scripts must use the model-specific parameter contract.
