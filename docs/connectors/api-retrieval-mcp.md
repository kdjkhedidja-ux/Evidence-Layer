# API-key retrieval, MCP, and token-reduction harness

Status: **implemented and enabled in production on 2026-11-12** with `EVIDENCE_RETRIEVE_API_ENABLED=true`. A synthetic workspace smoke returned selected evidence and `get_document` 200; the test account was purged afterward.

## Retrieval endpoint

`POST /v1/personal-api-keys/workspaces/{workspace_id}/retrieve`

Authentication: `Authorization: Bearer evl_pk_...`
Required API-key scope: `evidence:read`
The key is workspace-scoped; the existing API-key authorization RPC rechecks lifecycle `available`, active membership, expiry, revocation, and the 60/minute limit.

Request:

```json
{
  "query": "lexical retrieval context",
  "top_k": 5,
  "max_tokens": 1200,
  "max_chars": 6000,
  "candidate_limit": 50
}
```

`candidate_limit` is the newest evidence window read for the workspace, capped at 100. Selection is deterministic **Lexical** scoring; JEV is not used.

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

Selection remains lexical-only. Chunking is deterministic and happens on every request, so existing GitHub evidence is picked up immediately after deploy; operators do **not** need to re-sync for this change.

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
  ]
}
```

`score` is the adjusted Lexical score after deterministic path/title boosts. If the pinned tokenizer artifact is unavailable, `token_estimator` is `chars_div_4`; token counts are estimates in that mode.

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

## MCP server

A stdio MCP server calls the endpoints above and exposes:

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

- Service flag: `EVIDENCE_RETRIEVE_API_ENABLED=true` (default false; endpoint returns 404 while disabled).
- Deploy migration `20261112120000_personal_api_key_evidence_document.sql` for `get_document`.
- API key scope: `evidence:read`; workspace membership and lifecycle are enforced by the existing key RPC.
- No JEV, no Google Drive, no Marketplace work.
- Logs must not contain API keys or returned document content.
