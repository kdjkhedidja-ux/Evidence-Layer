import { NextRequest, NextResponse } from "next/server";

import { createNotionConnection } from "../../../../../lib/connector-api";
import { resolveNotionRedirectUri } from "../../../../../lib/notion-oauth";
import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";

const STATE_COOKIE = "notion_oauth_state";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/notion",
    maxAge: 0,
  });
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const storedState = request.cookies.get(STATE_COOKIE)?.value;
  if (url.searchParams.get("error") || !code || !state || state !== storedState) {
    return redirect(request, "/workspace?connector=notion&status=denied");
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind !== "authenticated") return redirect(request, "/login?next=/workspace");

  const clientId = process.env.NOTION_OAUTH_CLIENT_ID;
  const clientSecret = process.env.NOTION_OAUTH_CLIENT_SECRET;
  const redirectUri = resolveNotionRedirectUri({
    configured: process.env.NOTION_REDIRECT_URI,
    requestUrl: request.url,
  });
  if (!clientId || !clientSecret || !redirectUri) {
    return redirect(request, "/workspace?connector=notion&status=configuration");
  }

  let accessToken: string;
  let workspaceName: string | null = null;
  try {
    const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
    const tokenResponse = await fetch("https://api.notion.com/v1/oauth/token", {
      method: "POST",
      headers: {
        Authorization: `Basic ${credentials}`,
        "Content-Type": "application/json",
        "Notion-Version": "2026-03-11",
      },
      body: JSON.stringify({
        grant_type: "authorization_code",
        code,
        redirect_uri: redirectUri,
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    if (!tokenResponse.ok) return redirect(request, "/workspace?connector=notion&status=token");
    const token = (await tokenResponse.json()) as {
      access_token?: string;
      workspace_name?: string | null;
    };
    if (!token.access_token) return redirect(request, "/workspace?connector=notion&status=token");
    accessToken = token.access_token;
    workspaceName = token.workspace_name ?? null;
  } catch {
    return redirect(request, "/workspace?connector=notion&status=token");
  }

  const stored = await createNotionConnection(auth.accessToken, {
    access_token: accessToken,
    account_email: workspaceName,
    scopes: [],
  });
  if (!stored.ok) return redirect(request, "/workspace?connector=notion&status=store");
  return redirect(request, "/workspace?connector=notion&status=connected");
}
