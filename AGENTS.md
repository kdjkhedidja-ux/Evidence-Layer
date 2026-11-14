# Agent Instructions

## Evidence Layer MCP policy

This environment exposes the hosted MCP server `evidence-layer` with:

- `retrieve_evidence`
- `get_document`

Before answering any question about Evidence Layer, its behavior, architecture,
roadmap, retrieval, evaluation, costs, policies, or before changing documented
Evidence Layer behavior:

1. Call `retrieve_evidence` with a concise query derived from the task.
2. Use the returned evidence as the primary source.
3. Cite the selected `path` values when making claims about the repository.
4. If the evidence is insufficient, say so explicitly. Do not fill gaps from
   memory or outside knowledge.
5. Call `get_document` only when the full parent record is needed.
6. Never put API keys, tokens, or other secrets into a query, prompt, or log.

For routine commands, formatting-only edits, or unrelated tooling tasks, the
MCP lookup is optional. It is required whenever the answer depends on Evidence
Layer product, architecture, benchmark, retrieval, or policy facts.

If the MCP server is unavailable, report that clearly and continue only with
the evidence already present in the workspace; do not invent an answer.
