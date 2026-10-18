import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "../../../lib/supabase/server";
import { callbackFailureReason, safeInternalPath } from "../../../lib/supabase/redirect";
import { getSupabasePublicConfig } from "../../../lib/supabase/config";

function failure(request: Request, reason: string): NextResponse {
  return NextResponse.redirect(new URL(`/auth/error?reason=${reason}`, request.url));
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const providerError = url.searchParams.get("error");
  const errorCode = url.searchParams.get("error_code");

  if (providerError || errorCode || !code) {
    return failure(request, callbackFailureReason(providerError, errorCode));
  }
  if (!getSupabasePublicConfig()) return failure(request, "configuration");

  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return failure(request, "invalid-link");

    const { data, error: userError } = await supabase.auth.getUser();
    if (userError || !data.user) return failure(request, "invalid-link");

    const target = safeInternalPath(url.searchParams.get("next"), "/");
    return NextResponse.redirect(new URL(target, url.origin));
  } catch {
    return failure(request, "invalid-link");
  }
}
