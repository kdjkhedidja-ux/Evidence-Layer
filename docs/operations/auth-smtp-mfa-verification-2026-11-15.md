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

## Verification

| Check | Result |
|---|---|
| Signup (`POST /auth/v1/signup`) | `200`, `confirmation_sent_at` set |
| TOTP enroll (`POST /auth/v1/factors`) | `200`, factor and secret returned |
| TOTP challenge | `200`, challenge id returned |
| TOTP verify | `200`, new access token with `aal=aal2` |
| Test users | deleted after verification |

Signup with `example.com` is rejected by Supabase as
`email_address_invalid`; that is expected provider validation, not a product
outage. Use a real deliverable address.

## Residual

- Confirm the confirmation email arrives in the recipient inbox.
- A browser-level signup → confirm → sign-in → MFA smoke is still useful after
  the SMTP change.
- No repository secret was added; the SMTP password lives in the Auth provider
  configuration.
