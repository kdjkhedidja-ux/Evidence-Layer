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
      response.cookies.set(RECOVERY_COOKIE, recoveryProof(session.access_token), {
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
