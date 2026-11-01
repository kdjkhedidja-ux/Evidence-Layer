# Phase 8 — Frontend and Evidence Layer integration

## Product boundary

The root route remains the explicitly labelled **Demo / Synthetic** explorer.
Its five fictional records, fixture relationships, and reading guide are not
presented as customer data. The new /workspace route is separate and uses live
service responses only after the server verifies the Supabase Auth user.

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

## Not connected to live workspace data

- **Reading guide:** remains fixture-defined in the Demo / Synthetic
  explorer; the Evidence Service has no guide API. (Provenance links between
  evidence records and published source versions are now connected; broader
  relationship graphs are not.)
- **Verification:** source reads use the verified snapshot path, but there is
  no route for browsing verification status/history or verifier reports.
- **Evaluation:** Phase 6, Phase 7, and Phase 7B remain historical benchmark
  reports. They are not workspace evaluations.
- **Cost and performance:** the public benchmark page reports experiment
  estimates and measurements. No per-workspace metering, invoice, or live cost
  API is available.
- **Integrations:** no integration status/configuration endpoint is exposed.
- **Settings and access administration:** active memberships and roles are
  viewable, but invitations and role or membership changes are not exposed.

The workspace screen labels each item as connected, demo-only, read-only,
historical, or unavailable. These gaps are not silently filled with synthetic
or inferred values.

## Existing authorization limitation

The Evidence Service documentation identifies a temporary MVP policy in which
every active workspace member can read, create, update, and delete all evidence
records in that workspace. The frontend uses that existing service/RLS
contract and does not add a role bypass, but exposing write controls makes this
policy directly available through the product UI. The service README already
marks this policy as unsuitable for pre-production/public launch; replace it
with an owner-reviewed least-privilege capability model before broad release.
No invitation or role-management feature was added.

DenyAllSourceReadBoundary remains the default for untrusted core retrieval
contexts. The UI's verified snapshot request uses the Evidence Service's
separate trusted, request-scoped secure read path; neither the browser nor the
Next.js route constructs an allowing source boundary.

## Validation scope

Frontend route tests use synthetic session and service responses. Existing
Evidence Service tests cover membership, workspace isolation, RLS-shaped
denials, source grants, verified snapshot reads, and fail-closed behavior. They
do not establish that a hosted Supabase or Railway environment is configured
or reachable. This phase does not use production credentials or call a hosted
service.

Phase 6 and Phase 7 benchmark inputs, manifests, and results are outside this
change. The static benchmark pages remain unchanged.
