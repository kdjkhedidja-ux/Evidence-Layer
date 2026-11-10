# Authentication hardening — 2026-11-10

Status: **PASSWORD POLICY + MFA CHALLENGE IMPLEMENTED**. Production Auth settings were updated; the web changes are ready for release.

## Production Supabase Auth settings

| Setting | Before | After |
|---|---|---|
| `password_min_length` | 6 | **12** |
| `security_update_password_require_current_password` | false | **true** |
| `security_update_password_require_reauthentication` | false | **true** |
| `mailer_notifications_password_changed_enabled` | false | **true** |
| `mfa_allow_low_aal` | false | false (unchanged) |
| `mfa_totp_enroll_enabled` / `mfa_totp_verify_enabled` | true | true (unchanged) |

`password_hibp_enabled` could not be enabled: Supabase returns HTTP 402 because leaked-password protection requires the Pro plan. It remains **false** and must be revisited if the project is upgraded.

Password-reset recovery sessions are exempt from the current-password check in Supabase Auth, so the existing recovery flow remains valid. A recovery session is also considered recent for the reauthentication setting.

## Application changes

- `/mfa` page and client panel:
  - lists this account's TOTP factors;
  - enrolls a new authenticator (QR code + manual secret) and verifies it;
  - challenges an enrolled factor with a 6-digit code;
  - removes a factor only from an AAL2 session.
- Protected pages (`/`, `/workspace`, `/relationships`) redirect to `/mfa` when the verified session has an enrolled factor but is still AAL1.
- Evidence BFF routes return `403` with a two-factor message instead of forwarding an AAL1 token for an MFA-enrolled account.
- `VerifiedAuthContext` now carries the session expiry so the Workspace panel can display it.
- Workspace session panel adds **Sign out everywhere** (`signOut({ scope: "global" })`) and a link to `/mfa`.
- Capability catalog marks Security and Authentication sessions as **Partially available**.

## Deliberate limits

- Mandatory enrollment for accounts without any factor is **not enabled**. Supabase Auth has no project-wide enrollment requirement, and there are no MFA recovery codes; forcing enrollment would permanently lock out a user who loses the authenticator. A support/admin recovery process must exist first.
- No session inventory or per-device revocation: Supabase Auth exposes no supported endpoint for listing sessions or revoking one by session ID.
- Revoking refresh tokens does not invalidate an already-issued access token before its `exp` claim (currently 3600 seconds).

## Verification

- Web tests: 181 passed (including MFA helper, enrollment, and challenge tests).
- `npm run typecheck`, `npm run lint` (one pre-existing warning), and production build pass.
- Existing production account password login still succeeds after the policy change.
