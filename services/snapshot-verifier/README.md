# Snapshot Verifier process

This is a standalone, one-shot process. It is not part of the public Evidence
Service, has no HTTP listener, and accepts only workspace, source, and version
IDs. It obtains the staged claim through the verifier RPC, downloads the
private Storage object itself, computes SHA-256 and byte length, and publishes
only that computed evidence.

The current runner is deliberately loopback-only. It rejects non-loopback
Supabase URLs, never follows redirects, refuses `service_role` tokens, and
uses fixed resource limits of 8 MiB per object, 5 seconds per HTTP request,
and 30 seconds overall. Credentials are read only from the process environment
at startup; this code does not load `.env` files or log credential values.

## Local invocation

From the repository root, with Python 3.12+ and project service dependencies
installed, set these variables in the process environment:

- `SNAPSHOT_VERIFIER_SUPABASE_URL`: a local loopback Supabase URL.
- `SNAPSHOT_VERIFIER_PUBLISHABLE_KEY`: a publishable API key.
- `SNAPSHOT_VERIFIER_ACCESS_TOKEN`: an access token for the dedicated,
  non-human Auth identity that an operator has allowlisted for the workspace.

Then invoke the separate process:

```bash
PYTHONPATH=src:services/snapshot-verifier python -m snapshot_verifier_service \
  --workspace-id <workspace-uuid> \
  --source-id <source-uuid> \
  --version-id <version-uuid>
```

The command does not accept a client-supplied hash, size, object key, or token
argument. It prints only the resulting identifiers, calculated digest, and
size on success; failures use fixed reason codes. Missing or invalid runtime
configuration fails before any request. Local tests use fake gateways and
HTTPX mock transports and do not require Docker or Supabase.

This preparation does not create the Auth identity, allowlist entries, or
credentials, and it does not establish Storage's `id`/`version` semantics.
The loopback restriction is an intentional safety gate; a later deployment
review must decide and verify remote runtime networking and secret injection
before Railway configuration or deployment.
