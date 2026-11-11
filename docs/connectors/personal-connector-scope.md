# Personal connector scope — Google Drive first

Decision: **personal connector only** for the first production path.

## Product boundary

- A connection belongs to one authenticated user.
- Imported `connector_documents` are visible only to that user through owner-only RLS.
- No organization/workspace-wide connector, no admin consent, no shared-drive administration, and no sharing of imported documents with other workspace members.
- Users can connect their own Google account and run a manual, bounded sync.
- Disconnect removes the local connection/documents and attempts Google token revocation. Google revocation is best effort; if it fails, the local grant is still removed and the user can revoke it in their Google Account.

This is intentionally narrower than an enterprise connector. It does not claim shared-workspace evidence ingestion.

## Current readiness

| Stage | Ready? | Notes |
|---|---|---|
| Internal demo / test users | Yes, with a test OAuth client | Keep the OAuth app in testing mode and use explicit test users. |
| Public multi-user launch | No | Google restricted-scope verification and background sync/deletion work are still required. |
| Company/shared workspace | No | Requires organization connector, admin consent, shared-drive selection, ACL mapping, and audit. |

## What is already enforced

- `drive.readonly` scope; no write-back.
- AES-GCM token ciphertext at rest; key only in the service secret store.
- Forced RLS and owner-only policies for connections/documents.
- Refresh token preservation on reconnect when Google does not return a new refresh token.
- Best-effort provider revocation on disconnect.
- 2 MiB per-document cap; 10–25 documents per manual sync; text/binary content only.

## Required before public personal launch

1. **Google OAuth verification**
   - OAuth consent screen, verified app domain, privacy policy, terms, Limited Use disclosure.
   - Justification for `drive.readonly`; possible CASA/security assessment for restricted scopes.
   - Production and preview callback URIs registered exactly.
2. **Folder/file selection**
   - Let the user choose My Drive folders or explicitly include shared-with-me items; do not ingest an unbounded account by default.
3. **Background sync**
   - Worker/job queue, Drive Changes API cursor, pagination, backoff, per-user quota, and sync status.
4. **Source lifecycle**
   - Propagate Drive deletion/move/revocation; reconcile removed files; audit connect/sync/disconnect.
5. **Operational controls**
   - Per-user rate limits, connector failure metrics, token-encryption key rotation, support runbook.
6. **Disclosure**
   - Explain that imported documents are private to the connector owner and are not shared with workspace members.

## OAuth verification checklist

- [ ] Google Cloud project and Web application OAuth client created.
- [ ] Authorized JavaScript origin and exact redirect URI(s) registered.
- [ ] Consent screen: app name, support email, logo, privacy policy, terms, authorized domains.
- [ ] Scope justification for `https://www.googleapis.com/auth/drive.readonly`.
- [ ] Limited Use disclosure and data-deletion instructions.
- [ ] Test-user list for the pilot; app remains in testing mode until verification.
- [ ] Verification submitted; any CASA/security assessment completed if required.
- [ ] Production callback smoke with a dedicated test Google account.
- [ ] No OAuth client secret or encryption key in `NEXT_PUBLIC_*` or browser code.

## Rollout sequence

1. Pilot with test users and a testing-mode OAuth client.
2. Add folder selection and background sync.
3. Complete Google verification.
4. Run a dedicated-account connect → sync → document check → disconnect/revoke smoke.
5. Public personal launch.
6. Only later consider organization/shared-drive connectors.
