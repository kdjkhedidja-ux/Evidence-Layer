# Google OAuth verification package — Evidence Layer Drive connector

Status: **prepared, not submitted.** Submission requires the company's Google Cloud project, verified domain, legal identity/contact, demo video, and final legal pages. This package is the source material for the Google Cloud OAuth consent screen and Trust & Safety submission.

## App identity fields

| Field | Value to confirm |
|---|---|
| App name | Evidence Layer |
| User support email | **[insert monitored support email]** |
| Developer contact | **[insert monitored developer email]** |
| App logo | **[insert 120×120 PNG]** |
| Home page | `https://evidencelayer.dev` |
| Privacy policy | `https://evidencelayer.dev/privacy` |
| Terms of service | `https://evidencelayer.dev/terms` |
| Data deletion | `https://evidencelayer.dev/data-deletion` |
| Authorized domain | `evidencelayer.dev` |
| OAuth client type | Web application |
| Redirect URI | `https://evidencelayer.dev/api/connectors/google-drive/callback` |
| User type | **External** (public multi-user product) |
| Scope | `https://www.googleapis.com/auth/drive.readonly` |

Add preview/staging callback URIs only if they are actually used and verified. Do not submit a localhost URI for production verification.

## Scope justification

Use a specific, functionality-based justification. Draft:

> Evidence Layer uses `https://www.googleapis.com/auth/drive.readonly` so a signed-in user can explicitly connect their Google Drive and import only the documents they choose into their private Evidence Layer workspace for evidence search and provenance. The app never writes to Drive. A narrower `drive.file` scope would only cover files created or opened by the app and would not let users import existing Drive documents. `drive.metadata.readonly` would not provide the document content needed for the user-requested evidence workflow. We do not use Drive data for advertising, sale, or generalized AI/ML training.

If folder selection is implemented before submission, mention it explicitly: the app should ingest only selected folders/files, not the entire Drive.

## Data flow (for the reviewer)

1. User clicks **Connect Google Drive**.
2. Browser is redirected to Google OAuth; the app requests `drive.readonly` with offline access.
3. Google returns the authorization code to the BFF callback.
4. BFF exchanges the code server-side, then sends tokens over TLS to the Evidence Service.
5. The service encrypts tokens with AES-256-GCM; only ciphertext is stored in `connector_connections`.
6. On manual sync, the service decrypts the token in memory, calls Drive `files.list`/`files.get`/`files.export`, and writes normalized documents into owner-only `connector_documents`.
7. Access tokens may be refreshed with the stored refresh token and server-side OAuth client credentials.
8. Disconnect deletes the local connection/documents and attempts Google token revocation.
9. Account deletion cascades connector connections/documents and removes the Auth identity through the reviewed deletion worker.

No Google user data is sent to JEV, OpenAI, or another model provider by the current connector path.

## Limited Use disclosure

The public privacy page states:

> Evidence Layer's use of information received from Google APIs will adhere to the Google API Services User Data Policy, including the Limited Use requirements.

Operational commitments to maintain:

- no sale of Google user data;
- no advertising use;
- no generalized AI/ML training on Google user data;
- no human review except security/abuse/legal necessity;
- transfer only to infrastructure subprocessors under contract;
- deletion/disconnect path documented;
- least-privilege scope.

## Demo video script (2–3 minutes)

1. Show the public home page and sign-in.
2. Open the account panel and click **Connect Google Drive**.
3. Show the Google consent screen and explain the read-only scope.
4. Return to the app and show the connected Google Drive source.
5. Click **Sync** and show the resulting connector documents/status.
6. Show that imported documents are private to the user; no workspace sharing.
7. Click **Disconnect** and show the connection/documents removed.
8. Show the `/data-deletion` page and Google Account permissions link.
9. Explain that no data is written back to Drive and no model training occurs.

Record without real customer data. Use a dedicated test Google account.

## CASA readiness

Current controls:

- TLS for OAuth/service calls.
- AES-256-GCM token encryption at rest; key only in the service secret store.
- Forced RLS and owner-only policies for connector tables.
- Least-privilege Drive scope; no write-back.
- Size caps and bounded sync.
- Disconnect/account-deletion cascade.
- Token revocation best effort.
- No service-role credential in the user-facing connector path.

Gaps before a CASA assessment:

- [ ] Formal access-review and least-privilege procedure for production secrets.
- [ ] Key rotation and documented key custody.
- [ ] Connector audit log (connect/sync/disconnect/revoke) and retention.
- [ ] Incident-response and breach-notification runbook.
- [ ] Secure SDLC evidence, dependency scanning, vulnerability management.
- [ ] Vendor/subprocessor register and contracts.
- [ ] Data-retention schedule and tested deletion evidence.
- [ ] Penetration test or equivalent independent assessment.
- [ ] Backup/restore evidence that covers the connector data path.
- [ ] CASA assessor selected; fees and timeline approved.

## Submission checklist

- [ ] Company-owned Google Cloud project and billing.
- [ ] Verified domain and public home page.
- [ ] Final Privacy Policy, Terms, and Data Deletion pages with legal entity/contact.
- [ ] OAuth consent screen External with exact app identity.
- [ ] OAuth Web Client with production callback URI.
- [ ] Scope justification above and demo video.
- [ ] Test-user pilot completed without errors.
- [ ] Google Search Console domain verification if required.
- [ ] Submit for verification in Google Cloud Console.
- [ ] Respond to Trust & Safety; complete CASA when instructed.
- [ ] After approval: production smoke and annual recertification reminder.

## Current blockers

1. Legal entity, postal address, support/privacy contact, and effective date are still placeholders.
2. Privacy/Terms pages are drafts; Data Deletion references a support contact that is not yet finalized.
3. Connect/sync is local-only; migration 41 is not applied and the service/BFF are not deployed.
4. No live OAuth smoke with a real test Google account.
5. Folder selection, background sync, and deletion propagation are not implemented.
6. No CASA assessor, audit log, key rotation, or incident-response evidence yet.

Do not submit until blockers 1–4 are resolved. Blockers 5–6 may be completed during verification/CASA, but public launch should wait for them.
