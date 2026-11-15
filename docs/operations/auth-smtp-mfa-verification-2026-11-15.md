# Auth signup and MFA verification — 2026-11-15

Status: **signup email delivery configured; TOTP enroll/challenge/verify verified.**

## Root cause

Supabase Auth had `mailer_autoconfirm=false` and **no SMTP host configured**.
Signup created an unconfirmed user but public confirmation delivery was not
configured, and `mailer_allow_unverified_email_sign_ins` was false. New users
therefore could not complete account creation or reach MFA enrollment.

## Configuration

Supabase Auth SMTP now uses the verified Resend domain:

- host: `smtp.resend.com`
- port: `465`
- user: `resend`
- sender: `noreply@evidencelayer.dev`
- sender name: `Evidence Layer`
- password: the server-side `RESEND_API_KEY` (not committed)
- `mailer_autoconfirm` remains `false`
- `rate_limit_email_sent` raised from `2` to `30` per hour
- confirmation and recovery templates use direct `token_hash` links to
  `/auth/callback`, avoiding the PKCE code-verifier dependency when the email is
  opened in another browser/device

## Verification

| Check | Result |
|---|---|
| Signup (`POST /auth/v1/signup`) | `200`, `confirmation_sent_at` set |
| TOTP enroll (`POST /auth/v1/factors`) | `200`, factor and secret returned |
| TOTP challenge | `200`, challenge id returned |
| TOTP verify | `200`, new access token with `aal=aal2` |
| Confirmation callback | `307` to `https://evidencelayer.dev/` |
| Resend SMTP login/send | OK |
| Test users | deleted after verification |

Signup with `example.com` is rejected by Supabase as
`email_address_invalid`; that is expected provider validation, not a product
outage. Use a real deliverable address.

## Vercel verification

- Production domain `evidencelayer.dev` and `www.evidencelayer.dev` are verified.
- Production was `a3d6108` when the SMTP check ran; the current production
  deployment is `dpl_5jMmurkis8RvZjawEHoELBqiBsMk` from `a0ba8df` (PR #256).
- `NEXT_PUBLIC_SUPABASE_URL` points to the same production project
  (`slxfsjnsrvfkdtxayiud.supabase.co`).
- `NEXT_PUBLIC_NOTION_CONNECTOR_ENABLED=true`, Notion redirect URI is
  `https://evidencelayer.dev/api/connectors/notion/callback`, and
  `EVIDENCE_SERVICE_URL` points to the Railway production service.
- `/`, `/signup`, and `/login` return `200`; unauthenticated
  `/api/evidence/me` returns `401`.
- Vercel's Git branch lookup for `main` returned `git_branch_not_found` on
  2026-11-15. Production was deployed with the Vercel CLI from the repository
  root, which uploads the checked-out `main` source. PR previews still deploy.
  Inspect or reconnect the Git integration before relying on automatic
  production deployments.

## Signup existing-account message

PR #256 detects Supabase's empty-`identities` duplicate-signup response and
shows: "An account with this email already exists. Sign in instead, or reset
your password." The message replaces the form links with `/login` and
`/forgot-password`. A production Playwright check using a confirmed test
account returned the message and both links. This is a deliberate signup
enumeration tradeoff; password recovery remains non-enumerating.

## Residual

- The confirmation email now arrives and the link completes signup; a
  browser-level signup → confirm → sign-in → MFA smoke remains useful after
  future auth changes.
- No repository secret was added; the SMTP password lives in the Auth provider
  configuration.
