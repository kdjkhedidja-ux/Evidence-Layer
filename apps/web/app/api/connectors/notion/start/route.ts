import { randomBytes } from "node:crypto";

import { NextResponse } from "next/server";

import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";

const STATE_COOKIE = "notion_oauth_state";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export async function GET(request: Request): Promise<NextResponse> {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return redirect(request, "/login?next=/workspace");
  if (auth.kind === "unavailable") return redirect(request, "/workspace?connector=notion&status=unavailable");
  if (process.env.NEXT_PUBLIC_NOTION_CONNECTOR_ENABLED !== "true") {
    return redirect(request, "/workspace?connector=notion&status=coming_soon");
  }

  const clientId = process.env.NOTION_OAUTH_CLIENT_ID;
  if (!clientId) return redirect(request, "/workspace?connector=notion&status=configuration");
  const redirectUri =
    process.env.NOTION_REDIRECT_URI ??
    new URL("/api/connectors/notion/callback", request.url).toString();
  const state = randomBytes(32).toString("base64url");
  const url = new URL("https://api.notion.com/v1/oauth/authorize");
  url.searchParams.set("owner", "user");
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("state", state);

  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, state, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/notion",
    maxAge: 600,
  });
  return response;
}
