# Environment and Secrets Policy

## Rules

- The GitHub repository is public. No secrets in code, docs, issues, PRs,
  commit messages, or logs.
- `.env` files are never committed. Only `.env.example` files with variable
  names and no values are allowed.
- `.env` and `.env.*` are ignored by Git. `!.env.example` is the intentional
  exception: `.env.example` templates may be committed, with names only.
- Never paste secrets into an AI agent or chat, and do not ask an agent to
  read, print, or store them.
- Never log secrets, tokens, headers carrying credentials, or request/response
  bodies that might contain them.
- If a secret leaks, revoke and rotate it immediately, then review where it was
  exposed.
- Development, staging, and production use separate variables and separate
  credentials.

## Where variables live

| Platform | Allowed variables | Examples |
|---|---|---|
| Web app (for example Vercel) | Public project settings plus a private service URL | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, server-only `EVIDENCE_SERVICE_URL` |
| Supabase Auth dashboard | Hosted Auth SMTP configuration only | Resend SMTP password/API key, SMTP sender address; never place these in the web app |
| Evidence Service runtime | Server-only Supabase Data API/JWT verification settings | Existing `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_JWT_*` variables from its service `.env.example` |
| GitHub Codespaces | Encrypted Codespaces secrets | provider keys used for local work |

Supabase secret and service-role keys must never reach the frontend, a
`NEXT_PUBLIC_*` variable, or client-side code. Resend credentials belong only
in Supabase Auth's SMTP password field. This application does not call the
Resend API.

## Supabase Auth and Resend setup (manual)

No hosted Supabase Auth or Resend configuration is established by this code.
An authorized operator must configure it in the Supabase and Resend dashboards:

1. In Resend, add and verify the sending domain intended for authentication
mail, including the required DNS records. Use an address on that verified
domain, for example `Evidence Layer <no-reply@auth.your-verified-domain>`;
replace this placeholder with the domain the owner actually controls.
2. In Supabase **Authentication → SMTP Settings**, enable custom SMTP and set
   host `smtp.resend.com`, port `465` (SSL), username `resend`, and the Resend
   API key as the SMTP password. Set the sender email to the verified address
   and a suitable sender name. Keep the Resend API key only in this hosted
   secret field; do not copy it to this repository or an app environment.
3. In **Authentication → Providers → Email**, enable email/password sign-in
   and require email confirmation. The app fails closed if sign-up returns an
   active session before confirmation.
4. Use a separate non-production Supabase project for the first hosted test.
   In **Authentication → URL Configuration**, set that project's Site URL to
   the local or staging app origin and add only the matching non-production
   callback URLs (the `next` query string is part of the redirect target):
   `http://localhost:3000/auth/callback?next=%2F`,
   `http://localhost:3000/auth/callback?next=%2Freset-password`,
   `https://<staging-app-host>/auth/callback?next=%2F`, and
   `https://<staging-app-host>/auth/callback?next=%2Freset-password`. Replace
   `<staging-app-host>` with the approved non-production hostname. Do not add
   production URLs to this test project or use an unbounded redirect wildcard;
   configure production separately after review and approval.
5. Keep the confirmation template routed through Supabase's
   `{{ .ConfirmationURL }}`. On free-tier projects using Supabase's default
   email provider, the recovery template must also remain at
   `{{ .ConfirmationURL }}`: template edits are rejected by the hosted API.
   The web app requests recovery using an isolated implicit flow so the email
   can open in a different mobile browser. The existing callback receives the
   recovery session in the URL fragment and transfers it through a same-origin
   request before opening `/reset-password`. If a custom SMTP provider permits
   editing the template, the preferred recovery link is
   `https://evidence-layer.vercel.app/auth/recovery?token_hash={{ .TokenHash }}`.
   That page requires a button press before `/auth/callback` verifies the
   one-time token with `type=recovery`, limiting link-preview consumption. Use
   the approved app origin for each non-production project. Do not expose raw
   OTPs, token hashes, session tokens, or credentials in app logs.

Resend supports SMTP with host `smtp.resend.com`, username `resend`, and an API
key as its password; its current SMTP documentation recommends port 465 for
SSL. Verify these values against the Resend account before configuring a
project. The sender domain/address and app production origin are intentionally
not guessed here.

The checked-in `supabase/config.toml` enables local signup and email
confirmation through the local Mailpit capture server. Its higher email rate
limit is for the disposable local stack only. CI exercises signup, confirmation,
recovery, login, personal-workspace provisioning, and database isolation there.
Use the isolated hosted non-production project for a real Resend delivery check;
local Mailpit results do not establish hosted SMTP delivery.

## Web app variables

| Variable | Exposure | Purpose |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Public | Supabase project API URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Public | `sb_publishable_...` key used by Supabase Auth SDK |
| `EVIDENCE_SERVICE_URL` | Server-only | Evidence Service base URL used by same-origin Next.js route handlers |

Copy placeholders from `apps/web/.env.example`. The Resend SMTP password is
not an application variable. The service keeps its separate required JWT and
Data API configuration documented in
`services/evidence-service/.env.example`.
Set both `NEXT_PUBLIC_*` values in the app's build environment before building:
Next.js embeds public-prefixed variables in the browser bundle at build time.

## Hosted Staging authentication smoke test

The hosted smoke suite is separate from the mocked local browser tests. It
targets only the Staging Supabase project `slxfsjnsrvfkdtxayiud` and never
creates accounts or sends recovery mail. From `apps/web`, run:

```bash
npm run test:e2e:hosted
```

Provide these values through an authorized, temporary CI/Codespaces secret
store, not a checked-in file or command-line argument:

| Variable | Required for | Handling |
| --- | --- | --- |
| `STAGING_APP_URL` | Preview checks | HTTPS origin for the PR Preview |
| `STAGING_VERCEL_STORAGE_STATE` | Protected Preview | Path to a protected Playwright storage-state file containing the normal Vercel SSO session only; Supabase Auth state is rejected |
| `STAGING_ALLOW_AUTHENTICATED_PREVIEW_REQUESTS` | Authenticated Preview calls | Set to the exact value `I_CONFIRM_STAGING_PREVIEW_USES_STAGING_SUPABASE_AND_EVIDENCE_SERVICE` only after verifying the effective Vercel Preview variables target this Staging Supabase project and the approved Staging Evidence Service |
| `STAGING_TEST_EMAIL_A`, `STAGING_TEST_PASSWORD_A` | Authenticated smoke | Existing, authorized Staging identity A |
| `STAGING_TEST_EMAIL_B`, `STAGING_TEST_PASSWORD_B` | Workspace isolation | Existing, authorized Staging identity B |
| `STAGING_EVIDENCE_SERVICE_URL` | Service JWT rejection checks | HTTPS origin for the Staging Evidence Service |
| `STAGING_ALLOW_EVIDENCE_SERVICE_REQUESTS` | Direct service checks | Set to `I_CONFIRM_STAGING_EVIDENCE_SERVICE` only after verifying the target Railway service is Staging |
| `STAGING_EXPIRED_ACCESS_TOKEN` | Expiry rejection check | Optional previously issued Staging JWT, kept in the secret store |
| `STAGING_ALLOW_PERSONAL_WORKSPACE_PROVISIONING` | Workspace provisioning | Set to the exact value `I_AUTHORIZE_STAGING_PERSONAL_WORKSPACE_PROVISIONING` only after the account owners explicitly authorize this idempotent Staging mutation |

The app origin is deliberately restricted to the Vercel branch-preview hostname
for `fix/es256-p256-jwk-test-fixture`; custom domains and the production alias
are rejected. The service hostname must contain a `staging`, `stage`, `test`,
or `preview` label. These checks reduce accidental production targeting but
do not replace verification of the deployment SHA and effective platform
configuration.

For a protected Preview, authenticate normally through Vercel SSO in an
authorized browser and save the SSO-only state to a protected temporary file.
Storage states containing Supabase Auth sessions or Vercel protection-bypass
cookies are rejected.
Playwright Codegen can save the state without using browser developer tools:

```bash
npx playwright codegen --save-storage="$STAGING_VERCEL_STORAGE_STATE" "$STAGING_APP_URL"
```

Close the browser after normal SSO login. Do not share, print, commit, or
upload the storage-state file as a build artifact. Do not use a Vercel
protection-bypass token. The hosted suite disables traces, screenshots, and
video recording.

Authenticated Preview calls are blocked unless the operator has confirmed the
effective Vercel Preview targets. Browser Auth traffic is also intercepted:
only requests to the verified Staging Supabase origin with a publishable key
are allowed through, preventing accidental credential submission to another
Supabase project. Direct service checks require the separate Staging-target
confirmation.

Without the provisioning authorization flag, the suite intercepts the app's
workspace `PUT` locally and rejects it before it reaches the Preview. Once
authenticated Preview requests are explicitly confirmed, login and the
authenticated `GET /api/evidence/me` can be tested without provisioning. With
the exact authorization flag, the app may issue its idempotent
personal-workspace `PUT`; the test reports only the HTTP status. The isolation
test uses both existing accounts and requires B to have an active workspace;
it checks that A receives 404 for B's workspace. Invalid JWT testing is
read-only. Expired-token testing is skipped unless a previously issued JWT has
an expired `exp` claim and the Staging issuer; the token is never included in
output.

If any required configuration is unavailable, the hosted command fails before
test collection and lists missing variable names only; it does not silently
return a green run of skipped tests. If Vercel SSO blocks the runner, the
affected browser checks report `BLOCKED`/skipped and do not count as hosted
evidence. Account B and the expired JWT are optional and their corresponding
checks are skipped if absent. A Supabase Management API token is not used by
this suite. The hosted command does not create users, request password resets,
send email, or mutate data unless the explicit provisioning authorization is
present.

## Status

The repository now contains the local Supabase Auth frontend flow and an
Evidence Service identity/workspace boundary backed by Supabase Postgres RLS
migrations. The Explorer's evidence remains synthetic. This change does not
configure or verify a hosted Supabase project, SMTP delivery, production app
URLs, real confirmation/reset email delivery, or an authenticated hosted
end-to-end flow. The actual sender domain, callback allowlist, and active
Supabase JWT signing configuration must be confirmed by an authorized
operator before hosted verification.
