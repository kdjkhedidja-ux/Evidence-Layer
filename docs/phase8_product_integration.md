# Phase 8 — Frontend and Evidence Layer integration

## Product boundary

The root route is the live Evidence Explorer. It loads only authenticated,
workspace-scoped Evidence Service responses; it never substitutes fixture
records when a service is unavailable or a workspace is empty. The old
synthetic component is not routed from the product navigation. /workspace
remains the source/evidence management screen.

## Connected paths

| UI capability | Existing service contract | Current behavior |
| --- | --- | --- |
| Sign in, session, sign out | Existing Supabase Auth PKCE flow | Server components call auth.getUser() before using the session token. |
| Membership list | GET /v1/me | Next.js BFF forwards only the server-verified user's bearer token and checks the returned user_id. |
| Personal workspace setup | PUT /v1/me/personal-workspace | Same-origin only; the service derives the account identity and returns the result. |
| Workspace membership | GET /v1/workspaces/{workspace_id} and workspace-scoped evidence routes | The service resolves membership on each request; unknown or unauthorized workspaces are not disclosed. |
| Evidence records | GET, POST, PATCH, and DELETE /v1/workspaces/{workspace_id}/evidence... | The BFF validates UUIDs and response shape, allow-lists mutation fields, disables caching, and forwards the user token so Supabase RLS remains in force. |
| Verified source content | GET /v1/workspaces/{workspace_id}/sources/{source_id}/versions/{version_id}/content | The BFF does not read Storage directly. The service rechecks membership and the exact source grant before opening the pinned verified version and before returning bytes. The UI displays the service's SHA-256 Content-Digest when supplied. |
| Workspace source index | GET /v1/workspaces/{workspace_id}/sources | The service lists the snapshot versions visible to the caller under RLS (published versions of granted sources plus the caller's own staged rows) without exposing object keys. The UI groups versions by source and reads published versions from the list; no manually typed UUIDs are needed. |
| Evidence provenance links | source_id + source_version_id on the evidence routes | An evidence record may pin the exact published source version it was derived from. The service accepts a link only after the caller's own token resolves that version through the RLS-protected catalog; the database enforces the all-or-nothing pair and the same-workspace foreign key. |
| Evidence relationships | GET/POST/PATCH/DELETE /v1/workspaces/{workspace_id}/relationships | An additive RLS-protected table links two evidence records in the same workspace and records type, rationale, provenance note, creator, and last updater. Members read; active owners mutate. |
| Reading guide | Live stored evidence and source links | The guide describes how to inspect stored text and source provenance; it does not manufacture interpretations or unresolved facts. |
| Verification state | Published/staged source catalog state and verified source reader | Published rows are constrained to successful integrity verification. A separate attempt-by-attempt verification history is not available. |

The API routes are a same-origin browser boundary around existing endpoints;
they do not create a second evidence service or use service_role. Public
Supabase configuration remains limited to the URL and publishable key. The
Evidence Service URL remains server-only as EVIDENCE_SERVICE_URL.

The generic source-content endpoint accepts UUIDs supplied by an already
authenticated member. Those IDs do not authorize a read. The existing service
must independently verify identity, current workspace membership, source
ownership, exact version, current source grant, snapshot integrity, and access
again before delivery. A denial, unknown version, or unavailable dependency
returns no content. The UI has no fallback to the synthetic fixture or raw
Storage.

The source index route covers listing only; there is still no source-metadata
detail route beyond the index fields. This frontend does not invoke JEV or a
retrieval endpoint.

The service now selects its Supabase Storage adapter from the configured
project origin: plain HTTP on loopback uses the disposable local-stack
adapter, and an HTTPS non-loopback origin uses the hosted adapter. Both send
only the caller's verified bearer token plus the publishable key, refuse
redirects, compression, and oversize bodies, and fail closed; Storage RLS
decides object access. Hosted connectivity still requires the deployed
service to be configured against the target project and is not exercised by
this repository's test suites.

## Explicitly unavailable or historical capabilities

- **Evaluation:** Phase 6, Phase 7, and Phase 7B remain historical benchmark
  reports. They are not workspace evaluations.
- **Cost and performance:** the public benchmark page reports experiment
  estimates and measurements. No per-workspace metering, invoice, or live cost
  API is available.
- **Integrations:** no integration status/configuration endpoint is exposed.
- **Settings and access administration:** active memberships and roles are
  viewable, but invitations and role or membership changes are not exposed.
- **Source ingestion:** the UI can list and securely read published versions,
  but no hosted upload/publish integration for the trusted snapshot verifier is
  exposed. It does not stage or publish an unverified object.

The Explorer and Workspace label each item using its actual data state. These
gaps are not silently filled with synthetic or inferred values.

## Existing authorization limitation

Active workspace members can read evidence. Evidence create, update, and delete
and relationship mutations are restricted to active owners at both the
Evidence Service and RLS layers. This is intentionally conservative because
the current role model has no finer-grained capabilities. Invitations, role
changes, member administration, and source-upload authorization remain
unavailable; UI visibility is not the security boundary.

DenyAllSourceReadBoundary remains the default for untrusted core retrieval
contexts. The UI's verified snapshot request uses the Evidence Service's
separate trusted, request-scoped secure read path; neither the browser nor the
Next.js route constructs an allowing source boundary.

## Validation scope

Frontend route tests use synthetic session and service responses. Evidence
Service tests cover membership, workspace isolation, owner/member mutation
checks, relationship CRUD, source grants, verified snapshot reads, and
fail-closed behavior. pgTAP assertions cover relationship membership, owner
write policy, same-workspace foreign keys, and evidence member read-only access.
These tests do not establish that the hosted Supabase or Railway environment is
configured or reachable. No hosted production migration or deployment is
performed by the local change.

Phase 6 and Phase 7 benchmark inputs, manifests, and results are outside this
change. The static benchmark pages remain unchanged.
