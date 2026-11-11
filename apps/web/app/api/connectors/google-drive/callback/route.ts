import { NextRequest, NextResponse } from "next/server";

import { createGoogleDriveConnection } from "../../../../../lib/connector-api";
import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";

const STATE_COOKIE = "google_drive_oauth_state";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/google-drive",
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
    return redirect(request, "/workspace?connector=google_drive&status=denied");
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind !== "authenticated") {
    return redirect(request, "/login?next=/workspace");
  }

  const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET;
  const redirectUri =
    process.env.GOOGLE_OAUTH_REDIRECT_URI ??
    new URL("/api/connectors/google-drive/callback", request.url).toString();
  if (!clientId || !clientSecret) {
    return redirect(request, "/workspace?connector=google_drive&status=configuration");
  }

  let accessToken: string;
  let refreshToken: string | null;
  let expiresAt: string;
  let scopes: string[];
  let email: string | null = null;
  try {
    const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        code,
        grant_type: "authorization_code",
        redirect_uri: redirectUri,
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    if (!tokenResponse.ok) return redirect(request, "/workspace?connector=google_drive&status=token");
    const token = (await tokenResponse.json()) as {
      access_token?: string;
      refresh_token?: string;
      expires_in?: number;
      scope?: string;
    };
    if (!token.access_token) return redirect(request, "/workspace?connector=google_drive&status=token");
    accessToken = token.access_token;
    refreshToken = token.refresh_token ?? null;
    expiresAt = new Date(Date.now() + Math.max(0, token.expires_in ?? 3600) * 1000).toISOString();
    scopes = (token.scope ?? "").split(/\s+/).filter(Boolean);

    const userinfo = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
      headers: { Authorization: `Bearer ${accessToken}`, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (userinfo.ok) {
      const profile = (await userinfo.json()) as { email?: string };
      email = typeof profile.email === "string" ? profile.email : null;
    }
  } catch {
    return redirect(request, "/workspace?connector=google_drive&status=token");
  }

  const stored = await createGoogleDriveConnection(auth.accessToken, {
    access_token: accessToken,
    refresh_token: refreshToken,
    expires_at: expiresAt,
    scopes,
    account_email: email,
  });
  if (!stored.ok) return redirect(request, "/workspace?connector=google_drive&status=store");
  return redirect(request, "/workspace?connector=google_drive&status=connected");
}
