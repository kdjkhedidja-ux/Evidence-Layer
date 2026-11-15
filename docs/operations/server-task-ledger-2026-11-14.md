# Server task ledger — 2026-11-14

## Deployment

| Item | Value |
|---|---|
| Migration | `20261114210000_task_run_ledger.sql` applied to production |
| Evidence Service | Railway deployment `08461d1a-526b-449e-a67b-0d8c7089a543` — `SUCCESS` |
| Source commit | `27866c4c9473954096198f42b112cd46393400c5` |
| Flag | `EVIDENCE_TASK_LEDGER_ENABLED=true` |
| Table | `public.task_run_ledger` with active-workspace-member RLS |

## Stored data

The ledger stores only content-free retrieval metrics:

- run type and status;
- retrieval latency;
- selected tokens and characters;
- selected/chunk/relationship counts;
- scoring provider and fallback reason;
- `use_jev`.

It does **not** store task text, evidence content, answer text, provider
responses, or API keys. Nullable agent fields are reserved for the future
execution path.

## Production smoke

A disposable smoke workspace received one synthetic evidence row. A
task-context request used `use_jev=false` to avoid a provider call, then the
ledger was read and the evidence row was deleted.

| Check | Result |
|---|---|
| Evidence create | `201` |
| Task-context | `200`, `ok`, 1 selected, lexical |
| Task-runs read | `200`, 1 run |
| Stored run type | `task_context` |
| Stored status | `ok` |
| Retrieval latency | 3,641 ms |
| Selected tokens | 15 |
| Content fields present | no |
| Cleanup | evidence deleted |

## Endpoints

```text
POST /v1/workspaces/{workspace_id}/task-context
GET  /v1/workspaces/{workspace_id}/task-runs
GET  /api/evidence/workspaces/{workspace_id}/task-runs   (web BFF)
```

Both require a verified user session and workspace member RLS.

## Boundary

This implements the **retrieval-side ledger**. It does not yet execute an agent,
call an answer model for the user, calculate provider cost, or persist final
answer quality. Those remain separate steps.
