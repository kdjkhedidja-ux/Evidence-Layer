# API-key retrieval, MCP, and token-reduction harness

Status: **implemented and enabled in production on 2026-11-12** with `EVIDENCE_RETRIEVE_API_ENABLED=true`. A synthetic workspace smoke returned selected evidence and `get_document` 200; the test account was purged afterward.

## Retrieval endpoint

`POST /v1/personal-api-keys/workspaces/{workspace_id}/retrieve`

Authentication: `Authorization: Bearer evl_pk_...`
Required API-key scope: `evidence:read`; relationship expansion also requires
`relationships:read` when `top_k` is greater than one.
The key is workspace-scoped; the existing API-key authorization RPC rechecks lifecycle `available`, active membership, expiry, revocation, and the 60/minute limit.

Request:

```json
{
  "query": "lexical retrieval context",
  "top_k": 5,
  "max_tokens": 1200,
  "max_chars": 6000,
  "candidate_limit": 50,
  "use_jev": false
}
```

`use_jev` defaults to `false`. Setting it to `true` is request-level consent to
send the combined query and up to 50 budget-bounded candidate snippets to JEV.
The service must also have `EVIDENCE_JEV_ENABLED=true`; this flag defaults to
false. If either gate is closed, the response stays lexical and reports
`scoring_fallback_reason`. The Typesafe credential is read only by the service
from `TYPESAFE_API_KEY`.

`candidate_limit` is the newest evidence window read for the workspace, capped
at 100. Retrieval first selects lexical seed records, then reads up to 100
relationship edges and expands one hop to at most three linked records. A
linked record can be selected without a lexical match and receives 0.8 times
the adjusted score of its strongest selected seed. Seed records rank first;
all snippets share the same `top_k`, token, and character budgets. `top_k: 1`
skips relationship lookup. The response includes relation type, rationale,
and provenance only for edges whose endpoints are both selected. No second hop
is followed. Lexical scoring remains the default; JEV requires explicit
request-level and server-level opt-ins.

### Chunking and path priority

Retrieval chunks each evidence body at read time (no schema change and no re-sync required):

| Setting | Value |
|---|---:|
| Target chunk size | 2,400 characters (~500–800 visible tokens) |
| Overlap | 240 characters |
| Maximum chunks per evidence | 48 |
| Chunk identity | `evidence_id:start:end` (stable character offsets) |

Chunks are scored individually, then packed into `top_k`, `max_tokens`, and `max_chars`. Packing prefers one chunk per parent path first, then a second pass, then remaining chunks; this avoids filling the response from one huge file when other paths match. Each item preserves the parent `evidence_id` and adds `chunk_id`, `chunk_index`, `chunk_start`, `chunk_end`, `path`, and `title`.

Deterministic path/title multipliers (applied to the Lexical score):

- `README*` and `docs/ARCHITECTURE*`: **1.50×**
- `docs/product|design|overview|spec*`: **1.35×**
- other Markdown under `docs/`: **1.20×**
- top-level Markdown: **1.15×**
- query term hits the path or first heading: **1.08×** each
- closure/verification/smoke/checklist paths: **0.70×** only when the query has no ops term (audit, deploy, deletion, migration, runbook, worker, etc.), so real ops questions are not hidden

Lexical scoring and chunking are deterministic and happen on every request, so
existing GitHub evidence is picked up immediately after deploy; operators do
**not** need to re-sync for this change.

Response:

```json
{
  "status": "ok",
  "query": "lexical retrieval context",
  "workspace_id": "…",
  "top_k": 5,
  "max_tokens": 1200,
  "max_chars": 6000,
  "candidate_count": 12,
  "chunk_count": 31,
  "matched_chunk_count": 8,
  "selected_count": 2,
  "used_tokens": 280,
  "used_chars": 1100,
  "token_estimator": "o200k_base-tiktoken-0.14.0",
  "scoring_provider": "lexical_stopword_filtered",
  "scoring_fallback_reason": null,
  "chunking": "retrieve_time_v1",
  "evidence": [
    {
      "evidence_id": "…",
      "content": "…selected chunk…",
      "source_label": "GitHub: owner/repo/README.md",
      "source_id": null,
      "source_version_id": null,
      "score": 0.42,
      "token_count": 120,
      "char_count": 480,
      "chunk_id": "…:0:480",
      "chunk_index": 0,
      "chunk_start": 0,
      "chunk_end": 480,
      "path": "README.md",
      "title": "Evidence Layer"
    }
  ],
  "relationships": [
    {
      "id": "…",
      "evidence_id_a": "…",
      "evidence_id_b": "…",
      "relation_type": "supports",
      "rationale": "The release record supports the linked rollback procedure.",
      "provenance_note": "Manually reviewed both records."
    }
  ]
}
```

`scoring_provider` is `jev` only when every candidate score is returned and
validated. JEV scores the complete bounded candidate batch selected for that
request; SDK retries are disabled, and any provider, dependency, timeout, or
validation failure discards every JEV score and recomputes the complete
candidate set lexically. Rankings are never mixed. The at-most-50 candidates
are sent in one System One scoring request with an eight-second timeout and
SDK retries disabled. `scoring_fallback_reason` is a sanitized category and
is null when JEV was not requested or completed successfully.

`score` is the adjusted lexical score (or the discounted linked-seed score)
after deterministic path/title boosts when `scoring_provider` is lexical; it
is the provider relevance score when `scoring_provider` is `jev`.
`matched_chunk_count` counts lexical matches only; relationship-only chunks do
not inflate it. If the pinned
tokenizer artifact is unavailable, `token_estimator` is `chars_div_4`; token
counts are estimates in that mode.

## Workspace task-context preparation

The authenticated workspace UI can prepare evidence for a specific agent task
without requiring a personal API key in the browser:

`POST /v1/workspaces/{workspace_id}/task-context`

Authentication: the user's verified Supabase bearer token; current workspace
membership and RLS are checked by the service.

```json
{
  "task": "Review the release plan",
  "expected_outcome": "List evidence-backed risks and mitigations",
  "context_requirements": "Prioritize rollback steps and owner approvals",
  "top_k": 5,
  "max_tokens": 1200,
  "max_chars": 6000,
  "use_jev": false
}
```

The three task fields are combined as the retrieval query. Lexical scoring
remains the default; setting `use_jev` to `true` explicitly authorizes transfer
of that query and the bounded candidate snippets only if the service's
`EVIDENCE_JEV_ENABLED` flag is also open. JEV provider-side usage and cost are
not yet reported. The service
first finds lexical seed evidence, then expands one relationship hop to at most
three directly linked records. A related record can be selected even when its
text does not match the query; it inherits 0.8 times the adjusted lexical score
of the strongest selected seed it links to. Seeds are ranked before expanded
records, and expanded records remain subject to the same `top_k`, token, and
character limits. Expansion uses only persisted relationships and evidence
records returned for this workspace; it does not follow second-hop links.
The response includes the task brief, selected snippets, token and character
estimates, and only explicit relationship edges whose two evidence records are
both selected, including their type, rationale, and provenance note.

Selection stays inside `top_k`, `max_tokens`, and `max_chars`. Relationship
expansion uses only persisted workspace edges. The caller can copy the prepared
task and evidence bundle to an agent. The agent performs
the task; this route does not invoke the agent, persist a task run, or collect
model usage, cost, or outcome quality. The route is governed by the existing
`EVIDENCE_RETRIEVE_API_ENABLED` service flag.

### curl examples

```bash
export EL_API_URL="https://evidence-layer.example"
export EL_WORKSPACE_ID="00000000-0000-4000-8000-000000000000"
export EL_API_KEY="evl_pk_…"   # do not commit

curl -sS -X POST \
  "$EL_API_URL/v1/personal-api-keys/workspaces/$EL_WORKSPACE_ID/retrieve" \
  -H "Authorization: Bearer $EL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"lexical retrieval context","top_k":5,"max_tokens":1200,"max_chars":6000}'
```

```bash
curl -sS \
  "$EL_API_URL/v1/personal-api-keys/workspaces/$EL_WORKSPACE_ID/evidence/$EVIDENCE_ID" \
  -H "Authorization: Bearer $EL_API_KEY"
```

## Re-running the 15-question retrieval check

The A/B protocol used on 2026-11-13 is stored at
`tests/fixtures/retrieval_harness/ab_questions.jsonl`. It is a retrieval-quality
check first; calling DeepSeek is optional and only needed to compare answer
quality.

1. Export the same private workspace corpus from `connector_documents` (do not
   commit it; it may contain private repository text).
2. Run the retrieval harness with the same budgets as the production check:
   `top_k=8`, `max_tokens=3500`, `max_chars=14000`, `candidate_limit=100`.
3. Confirm the selected parent paths cover the expected sources:
   README/product chunks for the product question, `docs/ARCHITECTURE` for the
   architecture question, deletion/worker docs for the deletion question, and
   API/MCP docs for the agent question.
4. Check that selection stays within budget and that no single file supplies all
   `top_k` chunks while other paths match.
5. If DeepSeek keys are available, run the same questions with and without
   retrieved context, then inspect `usage.total_tokens` and answer sufficiency.

Chunking is retrieve-time only. A normal deploy is sufficient; do **not** force
a GitHub re-sync just to get chunking.

## Remote MCP (hosted, no local Python)

The hosted endpoint is a Streamable HTTP MCP server in the Evidence Service. It uses the same
personal API-key retrieve path, including bounded explicit relationship expansion, and exposes the same two tools:

- `retrieve_evidence(query, top_k, max_tokens, max_chars)`
- `get_document(evidence_id)`

Endpoint:

```text
POST https://<evidence-service>/mcp/workspaces/<workspace_id>/
Authorization: Bearer <personal API key>
Accept: application/json, text/event-stream
```

Use a workspace-scoped personal API key. The key is read-only, rate-limited, and validated against
the workspace in the URL. Source bytes, account operations, and writes are not exposed. The endpoint
is stateless and returns JSON responses; it is not an SSE-only local server.

MCP client configuration:

```json
{
  "mcpServers": {
    "evidence-layer": {
      "type": "http",
      "url": "https://<evidence-service>/mcp/workspaces/<workspace_id>/",
      "headers": {
        "Authorization": "Bearer <YOUR_PERSONAL_API_KEY>"
      }
    }
  }
}
```

Server and web flags are independent of the GitHub, Notion, and Google Drive connector flags:

| Surface | Variable | Default |
|---|---|---|
| Evidence Service | `EVIDENCE_REMOTE_MCP_ENABLED=true` | off |
| Web workspace | `NEXT_PUBLIC_REMOTE_MCP_ENABLED=true` | off |
| Web workspace URL | `NEXT_PUBLIC_REMOTE_MCP_URL=https://<evidence-service>/mcp` | unset |

When enabled, the workspace page shows **Remote MCP** above **Connected sources**, with the
workspace-scoped URL and a copyable client configuration. No local package installation is needed.
When disabled, the endpoint is not mounted and returns 404; the workspace page shows the capability
as not enabled.

## Python SDK

The same retrieve API has a small Python SDK for agents and backend jobs:

```python
from evidence_layer.sdk import EvidenceLayerClient, EvidenceApiError

client = EvidenceLayerClient(
    "https://evidence-layer.example",
    "evl_pk_…",
    "00000000-0000-4000-8000-000000000000",
)
context = client.retrieve(
    "What is the product?",
    top_k=5,
    max_tokens=1200,
    max_chars=6000,
    candidate_limit=50,
)
document = client.get_document(context["evidence"][0]["evidence_id"])
```

`retrieve` returns the chunk/path metadata documented above. `get_document` returns the full parent
document. Both methods raise `EvidenceApiError` on transport or non-2xx responses; API keys and
document content are never logged.

## Local stdio MCP server

A local stdio MCP server calls the endpoints above and exposes:

- `retrieve_evidence(query, top_k, max_tokens, max_chars)`
- `get_document(evidence_id)`

Run:

```bash
pip install -e .
pip install -r requirements-mcp.txt
export EVIDENCE_API_URL="https://evidence-layer.example"
export EVIDENCE_API_KEY="evl_pk_…"
export EVIDENCE_WORKSPACE_ID="00000000-0000-4000-8000-000000000000"
python -m evidence_layer.mcp.http_server
```

The API key stays in the server process. The MCP client receives only selected evidence/document JSON. Set `EVIDENCE_RETRIEVE_API_ENABLED=true` on the Evidence Service and redeploy it before use.

## Token-reduction harness

Fixture smoke:

```bash
PYTHONPATH=src:services/evidence-service python scripts/measure_retrieval_reduction.py \
  --corpus tests/fixtures/retrieval_harness/corpus.jsonl \
  --queries tests/fixtures/retrieval_harness/queries.jsonl \
  --output reports/retrieval-token-reduction-fixture.md
```

Live API measurement:

```bash
export EVIDENCE_API_URL="https://evidence-layer.example"
export EVIDENCE_API_KEY="evl_pk_…"
export EVIDENCE_WORKSPACE_ID="00000000-0000-4000-8000-000000000000"

python scripts/measure_retrieval_reduction.py \
  --corpus /tmp/github-synced-corpus.jsonl \
  --queries tests/fixtures/retrieval_harness/ab_questions.jsonl \
  --api-url "$EVIDENCE_API_URL" \
  --api-key "$EVIDENCE_API_KEY" \
  --workspace-id "$EVIDENCE_WORKSPACE_ID" \
  --top-k 8 --max-tokens 3500 --max-chars 14000 --candidate-limit 100 \
  --output reports/retrieval-token-reduction-github.md
```

Export a GitHub-synced corpus from `connector_documents` (operator/read-only SQL), one JSON object per line:

```sql
select json_build_object('id', external_id, 'text', content_text)
  from public.connector_documents
 where provider = 'github'
   and content_text is not null;
```

Queries file: one `{"query":"..."}` object per line. The report compares the full corpus token size with the selected API context size. The included fixture report is a harness smoke test, not a production GitHub measurement.

A/B token reports: [`first run`](../operations/el-vs-baseline-deepseek-tokens-2026-11-12.md), [`GitHub README run`](../operations/el-vs-baseline-deepseek-tokens-github-readme-2026-11-12.md), [`GitHub-synced whole-document run`](../operations/el-vs-baseline-deepseek-tokens-github-synced-2026-11-13.md), and [`chunked retrieve run`](../operations/el-vs-baseline-deepseek-chunked-2026-11-13.md).

## Enablement/config notes

- Service flags: `EVIDENCE_RETRIEVE_API_ENABLED=true` for retrieve/get-document, and `EVIDENCE_REMOTE_MCP_ENABLED=true` for the hosted MCP mount. Both default false and return 404 while disabled.
- JEV is separately gated by `EVIDENCE_JEV_ENABLED` (default false) and an explicit per-request `use_jev: true`. Candidate snippets and the query leave the Evidence Service only when both gates are open. Configure `TYPESAFE_API_KEY` as a server secret; do not expose it to the browser or logs.
- Web flags: `NEXT_PUBLIC_REMOTE_MCP_ENABLED=true` and `NEXT_PUBLIC_REMOTE_MCP_URL=https://<service>/mcp`; these are separate from connector flags.
- Deploy migration `20261112120000_personal_api_key_evidence_document.sql` for `get_document`.
- API key scope: `evidence:read`; workspace membership and lifecycle are enforced by the existing key RPC.
- JEV scoring is opt-in and disabled unless explicitly enabled by an operator; no live provider call is part of development or test validation.
- No Google Drive or Marketplace work.
- Logs must not contain API keys or returned document content.
