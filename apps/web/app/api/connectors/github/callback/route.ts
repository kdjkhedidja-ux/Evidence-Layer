import { NextRequest, NextResponse } from "next/server";

import {
  connectorMfaFailurePath,
  connectorMfaRedirectPath,
  connectorMfaStatus,
  createGitHubConnection,
} from "../../../../../lib/connector-api";
import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";
const STATE_COOKIE = "github_oauth_state";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/github",
    maxAge: 0,
  });
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  const storedState = request.cookies.get(STATE_COOKIE)?.value;
  const installationId = Number(url.searchParams.get("installation_id"));
  if (!state || state !== storedState || !Number.isSafeInteger(installationId) || installationId <= 0) {
    return redirect(request, "/workspace?connector=github&status=denied");
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind !== "authenticated") return redirect(request, "/login?next=/workspace");
  const mfaStatus = await connectorMfaStatus(auth);
  if (mfaStatus !== "not-required") {
    return redirect(request, connectorMfaRedirectPath("github", mfaStatus));
  }
  const stored = await createGitHubConnection(auth, installationId);
  const mfaFailure = await connectorMfaFailurePath("github", stored);
  if (mfaFailure) return redirect(request, mfaFailure);
  if (!stored.ok) return redirect(request, "/workspace?connector=github&status=store");
  return redirect(request, "/workspace?connector=github&status=connected");
}
