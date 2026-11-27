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
  "candidate_limit": 1000,
  "use_jev": true
}
```

`use_jev` defaults to `true` when the server JEV flag is enabled. Setting it to `false` forces the lexical path; JEV remains server-gated and request-scoped.
send the combined query and up to 50 budget-bounded candidate snippets to JEV.
The service must also have `EVIDENCE_JEV_ENABLED=true`; the code default is
false, while production is enabled. If the server gate is closed, the response
stays lexical and reports
`scoring_fallback_reason`. The Typesafe credential is read only by the service
from `TYPESAFE_API_KEY`.

**Whole-workspace prefilter (Step E, migration `20261127120000`).** Candidates are the newest `candidate_limit` records **plus** up to 200 full-text matches from the whole workspace, so an old record outside the newest window can still be selected. Both sets are then ranked by the same chunk scorer below; nothing else in selection changes. The three paths share it:

| Path | Read | Authorization |
|---|---|---|
| API-key retrieve, Remote MCP `retrieve_evidence` | `read_personal_api_key_evidence_candidates` with search **off** for the newest `candidate_limit + 1` rows (one signed call). Only if that extra row exists (the workspace is larger than the window), a second signed call adds the matches. | unchanged `authorize_personal_api_key_read('evidence', …)` proof; the query only narrows rows the key may already read |
| Signed-in task-context (web) | newest rows; `search_workspace_evidence` (`SECURITY INVOKER`) runs only when that read returned 1,000 rows or more | the caller's own RLS |

**Cost rule.** The search recomputes `to_tsvector` on the content of every matching row: about 0.8–1.3 s of database time on a production-size workspace (724 records, 8 M characters), against 30–90 ms for the newest-window read. It therefore runs only when the newest window is full; a workspace that fits the window is read once with search off, at the old cost. A later migration is planned to store the search vector so large workspaces stay fast. The log field `search` is `"skipped_window_not_full"`, `"workspace"` or `"newest_only"` (before the migration).

The prefilter uses a GIN expression index on `to_tsvector('simple', source_label || ' ' || content)` (no stemming, so identifiers and non-English text match as written; no new column). The query becomes an OR of at most 32 distinct terms of 2–64 characters, with English stop words dropped; arbitrary input never raises a syntax error. Matches are ranked with `ts_rank` over at most the 5,000 newest matching rows of the workspace, which bounds the cost of very common terms. Every `503` response, and every Remote MCP `unavailable` or `internal_error`, writes one content-free line `{"event": "service_unavailable", "surface": "http"|"remote_mcp", "route": "POST /v1/…/{workspace_id}/retrieve", "detail": <fixed message>, "cause": [exception class names, e.g. "PersonalApiKeyDataUnavailable", "httpx.ReadTimeout"], "upstream_status": <HTTP status or null>}`. Exception messages are never read, so no URL, identifier, query, key or content is logged. Before the migration is applied, the database answers `PGRST202` (function not found, nothing runs or is counted) and retrieval keeps the newest window only. The log line `retrieval_candidates_read` (and `retrieval_candidates_search` for task-context) reports `search: "workspace"` or `"newest_only"` and the number of `search_matches`, never the query.

`candidate_limit` is the newest evidence window read for the workspace. It
defaults to, and is capped at, 1000 records (the same window the signed-in web
app ranks), read in a single signed call.

**Counting.** Each signed read (`evidence` candidates, the optional `relationships` read when `top_k > 1`, and the search read for a workspace larger than the window) runs `authorize_personal_api_key_read` and counts **once each** against the per-key 60/minute limit: 1 to 3 per retrieval today. The per-user caps count **one** retrieval per request regardless of the number of signed reads (tests: `tests/test_evidence_search_prefilter.py`). Counting one per retrieval for the per-key limit needs a database change and is planned for the next migration window. Until migration `20261126130000` is applied the database
accepts at most 100, and the service falls back to that window automatically. Each retrieval writes two content-free JSON log
lines to stdout, `retrieval_candidates_read` (`limit`, `records`,
`legacy_fallback`, `read_ms`) and `retrieval_ranked` (`records`,
`candidate_chunks`, `selected`, `scoring_provider`, `rank_ms`), to track the
latency cost of the larger window. Retrieval first selects lexical seed records, then reads up to 100
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

Chunks are scored individually, then packed into `top_k`, `max_tokens`, and `max_chars`. Packing prefers one chunk per parent path first, then a second pass, then remaining chunks; this avoids filling the response from one huge file when other paths match. After the first selection, a chunk below **35% of the highest selected score** is not used merely to fill an empty `top_k` slot. Explicit seed/related records are exempt. Each item preserves the parent `evidence_id` and adds `chunk_id`, `chunk_index`, `chunk_start`, `chunk_end`, `path`, and `title`.

**Large-file parts.** A connector file longer than 65,536 characters is stored as several evidence records (parts, see [`README.md`](README.md)) whose `source_label` ends with `#part=i/n chars=start-end`. `path` drops that suffix, so all parts report the file's path. `chunk_start`/`chunk_end` stay relative to the part's content; the position in the original file is `start + chunk_start` to `start + chunk_end`, with `start` read from the label. Parts overlap by about 2,000 characters, and one response never returns the same file range twice: a chunk whose original-file range overlaps an already selected chunk of the same file is skipped. Only the first part of a file gets the intro-chunk boost.

Deterministic path/title multipliers (applied to the Lexical score):

- `README*` and `docs/ARCHITECTURE*`: **1.50×**
- `docs/product|design|overview|spec|prd*`: **1.35×**
- other Markdown under `docs/`: **1.20×**
- top-level Markdown: **1.15×**
- query term hits the path or first heading: **1.08×** each
- exact document title phrase in the query (for example `Evidence Layer`): **1.45×**
- canonical intro chunk (`README`, `PRD`, `ARCHITECTURE`, product/overview docs): **1.15×**; **1.10×** extra for definitional queries
- definitional queries (`what is`, `what are`, `define`, `explain`, `describe`, `overview`): ADR, roadmap, optimization-plan, development-plan, and implementation-audit paths receive **0.85×** unless the query also contains `adr`, `architecture`, `decision`, `design`, `plan`, or `roadmap`
- exact code identifier from the query found verbatim in a **code** chunk (`.py`, `.ts`, `.tsx`, `.sql`, …): **1.6×** for one identifier, **2.0×** for two or more. Identifiers are snake_case (`retrieve_evidence`), CamelCase with at least three capitalised segments (`ConnectorNotIndexedFile`) or camelCase with at least two after the lowercase start (`getVerifiedAuthContext`), at least 6 characters; matching is case-sensitive and whole-word. Brand words (GitHub, FastAPI, JavaScript) do not count. Documentation is never boosted or penalised. This is the only deterministic adjustment also applied to JEV scores, so code that defines a queried identifier is not outranked by prose describing it.
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
is the provider relevance score when `scoring_provider` is `jev`, multiplied by the exact-identifier boost for code chunks (otherwise unchanged).
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
  "use_jev": true
}
```

The three task fields are combined as the retrieval query. JEV is primary when
the server flag is open; setting `use_jev` to `false` forces the lexical fallback
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

### Use it from Kilo or another MCP client

Add the server to the client's MCP configuration. Keep the personal API key in the client secret
store or a user-only config file; never commit it.

```json
{
  "mcpServers": {
    "evidence-layer": {
      "type": "http",
      "url": "https://evidence-layer-production-c4b5.up.railway.app/mcp/workspaces/<workspace_id>/",
      "headers": {
        "Authorization": "Bearer <YOUR_PERSONAL_API_KEY>"
      }
    }
  }
}
```

For Kilo, the equivalent entry is under `mcp` in `~/.config/kilo/kilo.jsonc` with `type: "remote"`.
Restart Kilo or start a new session after adding the server so the tools are discovered.

Repeatable read-only smoke:

```bash
python scripts/smoke_remote_mcp.py
```

It reads `EL_API_KEY` and `EL_WORKSPACE_ID` from the environment or the Codespaces staged env file,
initializes the hosted MCP server, lists tools, calls `retrieve_evidence`, then calls `get_document`
and prints only metadata. It exits non-zero on an MCP/auth/tool failure.

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
    candidate_limit=1000,
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

A/B token reports: [`first run`](../operations/el-vs-baseline-deepseek-tokens-2026-11-12.md), [`GitHub README run`](../operations/el-vs-baseline-deepseek-tokens-github-readme-2026-11-12.md), [`GitHub-synced whole-document run`](../operations/el-vs-baseline-deepseek-tokens-github-synced-2026-11-13.md), [`chunked retrieve run`](../operations/el-vs-baseline-deepseek-chunked-2026-11-13.md), and [`multi-provider run`](../operations/el-vs-baseline-multi-provider-2026-11-14.md).

## Enablement/config notes

- Service flags: `EVIDENCE_RETRIEVE_API_ENABLED=true` for retrieve/get-document, and `EVIDENCE_REMOTE_MCP_ENABLED=true` for the hosted MCP mount. Both default false and return 404 while disabled.
- JEV is separately gated by `EVIDENCE_JEV_ENABLED` (code default false; enabled in production on 2026-11-14). It is primary by default when the server gate is open; `use_jev: false` forces lexical. Candidate snippets and the query leave the Evidence Service only when the server gate is open and JEV is selected. Configure `TYPESAFE_API_KEY` as a server secret; do not expose it to the browser or logs.
- Web flags: `NEXT_PUBLIC_REMOTE_MCP_ENABLED=true` and `NEXT_PUBLIC_REMOTE_MCP_URL=https://<service>/mcp`; these are separate from connector flags.
- Deploy migration `20261112120000_personal_api_key_evidence_document.sql` for `get_document`.
- API key scope: `evidence:read`; workspace membership and lifecycle are enforced by the existing key RPC.
- JEV scoring is server-gated and lexical fallback remains available; production was enabled on 2026-11-14 after a synthetic smoke (`docs/operations/provider-jev-activation-2026-11-14.md`).
- No Google Drive or Marketplace work.
- Logs must not contain API keys or returned document content.


## Per-user usage caps

Status: **implemented, not yet enabled in production** (migration
`20261127100000_user_usage_caps.sql` must be applied first).

Each signed-in user has one retrieval budget shared by the web task-context,
the API-key `retrieve` endpoint and Remote MCP `retrieve_evidence`, plus a
daily JEV budget. Counters live in `private.user_usage_counters`, per user (not
per key or workspace), in fixed UTC windows (one minute, one calendar day). A
refused call is not counted. `get_document` is not capped by these limits (the
per-key 60/minute read limit still applies).

| Variable | Default | Meaning |
|---|---|---|
| `EVIDENCE_USAGE_CAPS_ENABLED` | `false` | `true` enforces the caps. Leave `false` until the migration is applied. |
| `EVIDENCE_USER_RETRIEVAL_PER_MINUTE` | `30` | Retrievals per user per minute (task-context + API + MCP). |
| `EVIDENCE_USER_RETRIEVAL_PER_DAY` | `500` | Retrievals per user per UTC day. |
| `EVIDENCE_USER_JEV_PER_DAY` | `200` | JEV-scored retrievals per user per UTC day. |

`0` disables one cap. A missing, non-numeric or out-of-range value falls back
to the default (the conservative choice) and is logged.

**Retrieval cap exceeded** (`429`, `Retry-After` header):

```json
{"detail": {"code": "user_rate_limited", "window": "minute",
            "retry_after_seconds": 30,
            "message": "Per-user retrieval limit reached for this minute."}}
```

Remote MCP returns the same object as `{"error": {...}}` with
`code: "user_rate_limited"`. If the counter cannot be read, the call fails
closed with `503 {"detail": "Usage limits are unavailable"}`.

**JEV budget exhausted:** no error. The response keeps the complete lexical
ranking (JEV and lexical scores are never mixed) and reports
`scoring_provider: "lexical_stopword_filtered"` with
`scoring_fallback_reason: "jev_budget_exceeded"`
(`"jev_budget_unavailable"` if the budget could not be checked). The JEV
budget is spent only when JEV would actually run (opted in, server gate open,
at least one candidate).

On the API-key/MCP path the caps are sent to
`public.consume_personal_api_key_usage` inside an HMAC gateway proof
(`v1-usage` message, single-use nonce, 30-second window), verified like the
signed reads; this check does not spend the per-key read rate limit. The web
path calls `public.consume_user_usage` with the caller's own JWT, so a user can
only count against their own account.

## Task-run ledger

The authenticated workspace path records content-free retrieval telemetry when
`EVIDENCE_TASK_LEDGER_ENABLED=true` and the ledger migration is applied.

- write: automatic after a successful `POST /v1/workspaces/{workspace_id}/task-context`
- read: `GET /v1/workspaces/{workspace_id}/task-runs`
- auth: verified user session; workspace-member RLS
- stored: status, retrieval latency, selected tokens/chars, selected/chunk counts,
  relationship count, scoring provider, fallback reason, `use_jev`
- not stored: task text, evidence content, answer text, provider responses

The ledger schema also reserves nullable agent fields (`agent_provider`,
`agent_model`, input/output/total tokens, cost, latency, quality status/score)
for the later agent-execution path. The current writer is task-context only.
