import { NextResponse } from "next/server";
import { createSupabaseServerClient } from "../../../../lib/supabase/server";
import { RECOVERY_COOKIE, recoveryProof } from "../../../../lib/supabase/recovery";

export const dynamic = "force-dynamic";

function reject(status: number): NextResponse {
  return NextResponse.json({ error: "The recovery link is invalid or expired." }, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function hasRecoveryAuthenticationMethod(amr: unknown): boolean {
  return Array.isArray(amr) && amr.some((entry) =>
    entry === "recovery" ||
    (typeof entry === "object" && entry !== null && "method" in entry && entry.method === "recovery")
  );
}

export async function POST(request: Request): Promise<NextResponse> {
  if (request.headers.get("origin") !== new URL(request.url).origin ||
    request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
    return reject(403);
  }

  let accessToken: unknown;
  let refreshToken: unknown;
  try {
    const raw = await request.text();
    if (raw.length > 17000) return reject(400);
    const body = JSON.parse(raw);
    accessToken = body?.accessToken;
    refreshToken = body?.refreshToken;
  } catch {
    return reject(400);
  }
  if (typeof accessToken !== "string" || !accessToken || accessToken.length > 8192 ||
    typeof refreshToken !== "string" || !refreshToken || refreshToken.length > 8192) {
    return reject(400);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { data: claimsData, error: claimsError } = await supabase.auth.getClaims(accessToken);
    if (
      claimsError || !claimsData?.claims ||
      !hasRecoveryAuthenticationMethod(claimsData.claims.amr)
    ) {
      return reject(401);
    }

    const { data, error } = await supabase.auth.setSession({
      access_token: accessToken,
      refresh_token: refreshToken,
    });
    if (error || !data.session?.access_token || !data.user) return reject(401);
    const { data: verified, error: userError } = await supabase.auth.getUser();
    if (
      userError || !verified.user || verified.user.id !== data.user.id ||
      verified.user.id !== claimsData.claims.sub
    ) return reject(401);
    const { data: current, error: sessionError } = await supabase.auth.getSession();
    if (sessionError || !current.session?.access_token ||
      current.session.user.id !== verified.user.id) return reject(401);

    const response = NextResponse.json({ ok: true }, {
      headers: { "Cache-Control": "no-store" },
    });
    response.cookies.set(RECOVERY_COOKIE, await recoveryProof(current.session.access_token), {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 600,
    });
    return response;
  } catch {
    return reject(503);
  }
}
