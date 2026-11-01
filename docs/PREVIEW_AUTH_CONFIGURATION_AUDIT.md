# Preview authentication configuration audit

**Date:** 2026-10-31  
**Target:** Vercel Preview for `feat/product-integration`  
**Mode:** Read-only inspection; Production and Supabase settings were not changed.

## Findings

| Item | Finding | Status |
|---|---|---|
| Preview access | The branch Preview is protected by Vercel SSO. An unauthenticated request is redirected to Vercel's sign-in page. | Not inspectable from this workspace |
| `NEXT_PUBLIC_SUPABASE_URL` | Required by the app and embedded into the browser build. Effective Preview value and Vercel scope are unknown. | Not verified |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Required by the app; only `sb_publishable_...` keys are accepted. Effective Preview value and Vercel scope are unknown. | Not verified |
| `EVIDENCE_SERVICE_URL` | Required server-only setting for Next.js API routes. Effective Preview value and scope are unknown. | Not verified |
| Preview Supabase project | Cannot be read from the protected Preview bundle or Vercel settings. | Not verified |
| Evidence Service issuer | No Railway/service configuration access is available. | Not verified |
| Supabase Site URL and Redirect URLs | Not available from the public Auth settings response; no Supabase dashboard/API session is configured. | Not verified |
| Preview email confirmation | Not tested against the hosted project. | Not verified |

The Production app's public browser bundle contains a Supabase project reference and a publishable key. The reference is `slxfsjnsrvfkdtxayiud`; the repository's hosted-test documentation identifies that project as **Staging**. This read-only observation does not establish the Preview target and is not a recommendation to copy Production's configuration. The public Auth settings endpoint for that project reports email signup enabled and email confirmation required; it does not return Site URL or Redirect URL configuration.

The current application code expects `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` in the web build, and `EVIDENCE_SERVICE_URL` only on the server. Browser, server, and middleware clients read the same public Supabase configuration. The browser/server cookie options use `path=/`, `sameSite=lax`, and `secure` for production HTTPS. The callback is `/auth/callback`; its redirect target is built from the current app origin.

The local E2E suite now exercises the browser cookie through the server-side identity and Workspace APIs using synthetic local services. It passed locally, including unauthenticated and non-member denial cases. This does not prove the hosted Preview's environment variables, callback allow-list, issuer, or cookies are correct.

## Access checks performed

- The Vercel deployment is visible as a successful Preview check on PR #48, but the application URL is SSO-protected.
- Vercel CLI is not installed and no Vercel configuration is linked in the workspace.
- Supabase CLI is installed but has no Supabase account session; its read-only project listing returned `AccessTokenRequiredError`.
- No environment values, keys, passwords, or session tokens were printed or used for administrative access.
- The hosted Playwright command stopped before test collection because its secure runtime inputs are absent: `STAGING_APP_URL`, `STAGING_VERCEL_STORAGE_STATE`, `STAGING_TEST_EMAIL_A`, `STAGING_TEST_PASSWORD_A`, and `STAGING_EVIDENCE_SERVICE_URL`.

## Required owner actions

1. In the Vercel project's Environment Variables settings, confirm that all three required variables exist for Preview. Confirm whether each applies to every Preview branch or specifically to `feat/product-integration`. Inspect branch-specific overrides as well as the general Preview entries; a branch-specific variable overrides the general Preview value. Vercel applies environment changes only to new deployments. See [Vercel environment variables](https://vercel.com/docs/environment-variables) and [managing variables across environments](https://vercel.com/docs/environment-variables/manage-across-environments).
2. Without sharing values, verify that the two public Supabase variables identify the same approved non-production project. Verify that the Preview `EVIDENCE_SERVICE_URL` is the approved Staging service and that the service's configured JWT issuer matches that Supabase project. Do not use `service_role` or a secret key in the web app.
3. In that same non-production Supabase project's Auth URL Configuration, confirm the approved Staging Site URL and allow the two callback targets for the current branch Preview:

   - `https://evidence-layer-git-feat-product-integration-said-team-0ddcf7f0.vercel.app/auth/callback?next=%2F`
   - `https://evidence-layer-git-feat-product-integration-said-team-0ddcf7f0.vercel.app/auth/callback?next=%2Freset-password`

   These targets follow the callback URLs used by this app. Supabase requires redirect targets to match its allow-list; set the Site URL to the approved Staging canonical origin rather than inferring it from Production. See [Supabase redirect URL configuration](https://supabase.com/docs/guides/auth/redirect-urls).
4. After the effective Preview variables and Staging Auth settings are confirmed, create a new Preview deployment for `feat/product-integration`. Existing deployments do not receive environment changes.
5. Run the hosted suite using an authorized, SSO-only browser state and an existing Staging test account through the protected secret store. The current hosted suite covers sign-in, session-backed workspace identity, unauthenticated rejection, cross-workspace denial, and invalid JWT rejection. It does not create accounts or send confirmation mail. A real hosted sign-up/confirmation test still needs an approved disposable mailbox and a safe confirmation-link retrieval method; none is configured here.

No Production deployment, Production Supabase setting, database policy, JWT verification, RLS rule, Source ACL, or PR merge was performed.
