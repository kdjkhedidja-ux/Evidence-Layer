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
4. In **Authentication → URL Configuration**, set the production Site URL to
   the actual app origin and add these exact full callback URLs (the `next`
   query string is part of the redirect target):
   `http://localhost:3000/auth/callback?next=%2F`,
   `http://localhost:3000/auth/callback?next=%2Freset-password`,
   `https://<actual-app-host>/auth/callback?next=%2F`, and
   `https://<actual-app-host>/auth/callback?next=%2Freset-password`. Replace
   `<actual-app-host>` with the deployed app hostname. Do not use an unbounded
   redirect wildcard.
5. Keep the confirmation and password recovery templates routed through
   Supabase's `{{ .ConfirmationURL }}` so Supabase verifies the one-time link
   and redirects back to the configured app callback. Do not expose raw OTPs
   or credentials in app logs.

Resend supports SMTP with host `smtp.resend.com`, username `resend`, and an API
key as its password; its current SMTP documentation recommends port 465 for
SSL. Verify these values against the Resend account before configuring a
project. The sender domain/address and app production origin are intentionally
not guessed here.

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

## Status

The repository now contains the local Supabase Auth frontend flow and an
Evidence Service identity/workspace boundary backed by Supabase Postgres RLS
migrations. The Explorer's evidence remains synthetic. This change does not
configure or verify a hosted Supabase project, SMTP delivery, production app
URLs, real confirmation/reset email delivery, or an authenticated hosted
end-to-end flow. The actual sender domain, callback allowlist, and active
Supabase JWT signing configuration must be confirmed by an authorized
operator before hosted verification.
