import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "../../../lib/supabase/server";
import { callbackFailureReason, safeInternalPath } from "../../../lib/supabase/redirect";
import { getSupabasePublicConfig } from "../../../lib/supabase/config";
import { provisionPersonalWorkspaceForSession } from "../../../lib/evidence-api";
import { RECOVERY_COOKIE, recoveryProof } from "../../../lib/supabase/recovery";

function failure(request: Request, reason: string): NextResponse {
  const response = NextResponse.redirect(new URL(`/auth/error?reason=${reason}`, request.url));
  response.headers.set("Cache-Control", "no-store");
  return response;
}

function implicitRecoveryPage(): NextResponse {
  const response = new NextResponse(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Verify recovery link · Evidence Layer</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; background: #0d141a; color: #ecf1f2; }
    main { box-sizing: border-box; width: min(100%, 28rem); padding: 2.5rem 1.5rem; }
    p { color: #b7c5ca; line-height: 1.6; }
    a { color: #80d8bb; }
  </style>
  <script src="/recovery-complete.js" defer></script>
</head>
<body>
  <main>
    <h1>Verifying your recovery link</h1>
    <p>Please wait while we open the password reset page.</p>
    <p>If this page does not continue, <a href="/forgot-password">request a new link</a>.</p>
  </main>
</body>
</html>`, {
    status: 200,
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
  return response;
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const codeValues = url.searchParams.getAll("code");
  const flowIdValues = url.searchParams.getAll("sb_flow_id");
  const tokenHashValues = url.searchParams.getAll("token_hash");
  const typeValues = url.searchParams.getAll("type");
  const code = codeValues[0];
  const flowId = flowIdValues[0];
  const tokenHash = tokenHashValues[0];
  const otpType = typeValues[0];
  const providerError = url.searchParams.get("error");
  const errorCode = url.searchParams.get("error_code");

  // The default Supabase email template verifies the link before redirecting
  // here. Implicit recovery tokens arrive in the URL fragment, unseen by this
  // server route; a same-origin browser script hands them to the guarded API.
  if (
    url.searchParams.size === 1 &&
    url.searchParams.get("next") === "/reset-password" &&
    codeValues.length === 0 && flowIdValues.length === 0 &&
    tokenHashValues.length === 0 && typeValues.length === 0
  ) {
    return implicitRecoveryPage();
  }

  const hasCode = codeValues.length === 1 && Boolean(code);
  const hasOtp = tokenHashValues.length === 1 && Boolean(tokenHash) &&
    (otpType === "email" || otpType === "recovery") && typeValues.length === 1;
  const isCodeFlow = hasCode && tokenHashValues.length === 0 && typeValues.length === 0 &&
    flowIdValues.length <= 1 && (flowIdValues.length === 0 || Boolean(flowId));
  const isOtpFlow = hasOtp && codeValues.length === 0 && flowIdValues.length === 0;
  if (providerError || errorCode || isCodeFlow === isOtpFlow) {
    return failure(request, callbackFailureReason(providerError, errorCode));
  }
  if (!getSupabasePublicConfig()) return failure(request, "configuration");

  try {
    const supabase = await createSupabaseServerClient();
    const { data: exchangeData, error: exchangeError } = hasCode
      ? flowId
        ? await supabase.auth.exchangeCodeForSession(code ?? "", { flowId })
        : await supabase.auth.exchangeCodeForSession(code ?? "")
      : await supabase.auth.verifyOtp({
        token_hash: tokenHash ?? "",
        type: otpType as "email" | "recovery",
      });
    if (exchangeError || !exchangeData?.session?.access_token) {
      return failure(request, "invalid-link");
    }
    const session = exchangeData.session;

    const { data, error: userError } = await supabase.auth.getUser();
    if (userError || !data.user || session.user.id !== data.user.id) {
      return failure(request, "invalid-link");
    }

    const isRecovery = hasCode
      ? "redirectType" in exchangeData && exchangeData.redirectType === "recovery"
      : otpType === "recovery";
    const requestedTarget = safeInternalPath(url.searchParams.get("next"), "/");
    const target = isRecovery ? "/reset-password" :
      new URL(requestedTarget, url.origin).pathname === "/reset-password" ? "/" : requestedTarget;
    if (
      target !== "/reset-password" &&
      !(await provisionPersonalWorkspaceForSession(session.access_token))
    ) {
      return failure(request, "workspace-provisioning");
    }

    const response = NextResponse.redirect(new URL(target, url.origin));
    response.headers.set("Cache-Control", "no-store");
    if (target === "/reset-password") {
      response.cookies.set(RECOVERY_COOKIE, await recoveryProof(session.access_token), {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
        path: "/",
        maxAge: 600,
      });
    }
    return response;
  } catch {
    return failure(request, "invalid-link");
  }
}
