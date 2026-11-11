import { randomBytes } from "node:crypto";

import { NextResponse } from "next/server";

import { getVerifiedAuthContext } from "../../../../../lib/supabase/server";

export const dynamic = "force-dynamic";

const STATE_COOKIE = "google_drive_oauth_state";
const SCOPE = "https://www.googleapis.com/auth/drive.readonly";

function redirect(request: Request, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url));
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export async function GET(request: Request): Promise<NextResponse> {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return redirect(request, "/login?next=/workspace");
  if (auth.kind === "unavailable") return redirect(request, "/workspace?connector=google_drive&status=unavailable");

  if (process.env.NEXT_PUBLIC_GOOGLE_DRIVE_CONNECTOR_ENABLED !== "true") {
    return redirect(request, "/workspace?connector=google_drive&status=coming_soon");
  }
  const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
  if (!clientId) return redirect(request, "/workspace?connector=google_drive&status=configuration");

  const redirectUri =
    process.env.GOOGLE_OAUTH_REDIRECT_URI ??
    new URL("/api/connectors/google-drive/callback", request.url).toString();
  const state = randomBytes(32).toString("base64url");
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", SCOPE);
  url.searchParams.set("access_type", "offline");
  url.searchParams.set("prompt", "consent");
  url.searchParams.set("include_granted_scopes", "true");
  url.searchParams.set("state", state);

  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(STATE_COOKIE, state, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/api/connectors/google-drive",
    maxAge: 600,
  });
  return response;
}
