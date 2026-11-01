# apps/web

Next.js (App Router, TypeScript) interface for the synthetic Evidence Explorer
and the Supabase Auth personal-workspace foundation. Supabase Auth is the only
identity provider. The app uses cookie-backed PKCE sessions through
`@supabase/ssr`; it does not implement passwords, JWT signing, or a second
identity system.

The root Evidence Explorer stays a clearly labelled synthetic demo. The
/workspace page reads the verified user's memberships and RLS-protected
evidence records through same-origin Next.js API routes and the existing
Evidence Service. The browser never receives a Supabase access token for these
requests. Source snapshot bytes are read only through the existing service
endpoint that rechecks workspace membership, source grant, and the verified
snapshot before opening and before delivery. A denied or unavailable read has
no fallback.

Live relationships, a live reading guide, verifier history, workspace
evaluation and metering, integration management, invitations, and role
administration are not exposed by the current service contract. See
[the Phase 8 integration record](../../docs/phase8_product_integration.md).

## Vercel

Set **Root Directory** to `apps/web`. Configure `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` as public frontend values and
`EVIDENCE_SERVICE_URL` as a server-only value. Do not add Supabase secret or
service-role keys to this app. See
[docs/ENVIRONMENT_AND_SECRETS.md](../../docs/ENVIRONMENT_AND_SECRETS.md) for
the external Supabase Auth and Resend setup that remains manual.

## Local development

```bash
cd apps/web
npm install
npm run dev
```

The web app uses the values in `.env.example`; copy them to an ignored local
`.env.local` and fill them using non-production test credentials. Keep email
confirmation enabled in Supabase Auth. The app deliberately signs a user out
if sign-up unexpectedly returns an active session, rather than bypassing email
verification.

## Checks

```bash
npm test
npm run typecheck
npm run lint
npm run build
npm run test:integration:local
```

Auth callback URL: `/auth/callback`. Set the Supabase Site URL and exact full
callback URLs (including `?next=%2F` or `?next=%2Freset-password`) in the Auth
URL Configuration. Email confirmation returns through this PKCE callback.
Password recovery requests use an isolated implicit-flow client because the
default hosted `{{ .ConfirmationURL }}` template redirects into the browser that
opens the email. The callback receives recovery tokens in the URL fragment,
removes that fragment, and establishes a verified, short-lived recovery session
before opening `/reset-password`. Other Auth flows remain PKCE. When a custom
SMTP provider permits editing the recovery template, it can instead link to
`/auth/recovery?token_hash={{ .TokenHash }}` on the app origin. That page waits
for a button press before verifying the token hash, which also limits link
preview consumption.
The integration command requires the local Supabase stack started from the
repository root; it reads CLI status and uses only local Mailpit messages.
