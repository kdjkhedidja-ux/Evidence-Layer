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

## Dogfooding during development

This repository is also the Evidence Layer test environment. Every non-trivial
development task should exercise the hosted MCP while the product is being
built:

1. Before starting, call `retrieve_evidence` with a concise query derived from
   the task.
2. Inspect the selected paths and scores. If a file that clearly belongs to the
   task is missing from the result, treat it as a retrieval-quality finding.
3. Use the selected evidence to guide the implementation, then verify with the
   relevant repository tests.
4. In the final response, include a short `EL selection` note with:
   - the query;
   - selected paths;
   - whether the selection was sufficient;
   - any missing or noisy result worth investigating.
5. If the task reveals a ranking gap, add a regression test or a focused fix
   instead of only working around it.
6. Do not let an MCP outage block code work. Report the outage and continue from
   repository evidence, but mark the dogfooding step as skipped.

For pure status checks, formatting-only commands, or secret-handling operations,
the MCP call may be skipped. It is required for every non-trivial code, test,
documentation, retrieval, architecture, or product task.

If the MCP server is unavailable, report that clearly and continue only with
the evidence already present in the workspace; do not invent an answer.
