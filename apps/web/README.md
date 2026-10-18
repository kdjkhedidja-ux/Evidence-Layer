# apps/web

Next.js (App Router, TypeScript) interface for the synthetic Evidence Explorer
and the Supabase Auth personal-workspace foundation. Supabase Auth is the only
identity provider. The app uses cookie-backed PKCE sessions through
`@supabase/ssr`; it does not implement passwords, JWT signing, or a second
identity system.

The Evidence Explorer continues to display local synthetic evidence. The
sidebar separately reads the signed-in user's authorized workspace list from
the Evidence Service and offers explicit personal-workspace provisioning.
There is no evidence retrieval or AI answer generation in this frontend.

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
```

Auth callback URL: `/auth/callback`. Set the Supabase Site URL and exact full
callback URLs (including `?next=%2F` or `?next=%2Freset-password`) in the Auth
URL Configuration. Email confirmation and password reset both return through
this PKCE callback.
