import { randomBytes } from "node:crypto";

import { NextResponse } from "next/server";

import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";
const STATE_COOKIE = "github_oauth_state";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export async function GET(request: Request): Promise<NextResponse> {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return redirect(request, "/login?next=/workspace");
  if (auth.kind === "unavailable") return redirect(request, "/workspace?connector=github&status=unavailable");
  if (process.env.NEXT_PUBLIC_GITHUB_CONNECTOR_ENABLED !== "true") {
    return redirect(request, "/workspace?connector=github&status=coming_soon");
  }
  const slug = process.env.GITHUB_APP_SLUG;
  if (!slug) return redirect(request, "/workspace?connector=github&status=configuration");
  const state = randomBytes(32).toString("base64url");
  const url = new URL(`https://github.com/apps/${encodeURIComponent(slug)}/installations/new`);
  url.searchParams.set("state", state);
  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, state, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/github",
    maxAge: 600,
  });
  return response;
}
