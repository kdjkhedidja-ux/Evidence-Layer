# Task-context production smoke — 2026-11-14

Read/write scope: one disposable personal smoke workspace. Two synthetic evidence rows and one
synthetic relationship were created, used for the task-context request, then deleted. No customer
workspace, connector, flag, migration, or worker setting was changed.

## Deployment under test

| Item | Value |
|---|---|
| Evidence Service | Railway deployment `6e958cea-5b25-4f22-aa71-6e9bac725b49` — `SUCCESS` |
| Source commit | `6b01587011a9d6c90139f6f5c4ef91f4ab8024af` |
| Web production | Vercel deployment `dpl_BWRhGy7pNiRPnAcHJKoZNauFt5R8` — `READY` |
| Endpoint | `POST https://evidence-layer-production-c4b5.up.railway.app/v1/workspaces/{workspace_id}/task-context` |
| Auth | Verified smoke-user session; no API key used |
| Retrieval | Lexical, `top_k=3`, `max_tokens=800`, `max_chars=3000`, `use_jev=false` |

## Result

| Check | Result |
|---|---|
| HTTP status | `200` |
| Retrieval status | `ok` |
| Selected evidence | `2` synthetic records |
| Evidence tokens | `34` estimated tokens |
| Evidence characters | `134` |
| Chunking | `retrieve_time_v1` |
| Scoring provider | `lexical_stopword_filtered` |
| JEV fallback reason | `null` (JEV not requested) |
| Both related records selected | yes |
| Relationships returned | `1` — `contextualizes` |
| Relationship rationale/provenance | returned |
| Cleanup | relationship deleted; evidence rows deleted; remaining evidence `0` |

The three task fields were combined into the retrieval query, both directly linked records were
selected, and the relationship was returned only because both endpoints were in the selected
context. The response stayed inside the requested token and character budgets.

## Meaning

Task-focused retrieval is operational in production, not only in CI:

- the authenticated workspace endpoint is deployed and reachable;
- it uses the same bounded chunked retrieval path as the agent API;
- relationship expansion and relationship provenance work end-to-end;
- JEV remained off and no external scoring provider was called;
- synthetic production data was removed after the check.

This smoke does **not** claim agent execution, answer quality, per-task token/cost telemetry, or
JEV production operation.
