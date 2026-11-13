import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { getSupabasePublicConfig } from "./lib/supabase/config";
import { hasRecoveryProof, RECOVERY_COOKIE } from "./lib/supabase/recovery";

function redirectTo(request: NextRequest, path: string, source?: NextResponse): NextResponse {
  const redirected = NextResponse.redirect(new URL(path, request.url));
  if (source) {
    for (const cookie of source.cookies.getAll()) redirected.cookies.set(cookie);
    for (const header of ["cache-control", "expires", "pragma"]) {
      const value = source.headers.get(header);
      if (value) redirected.headers.set(header, value);
    }
  }
  return redirected;
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const pathname = request.nextUrl.pathname;
  const protectedReset = pathname === "/reset-password";
  const config = getSupabasePublicConfig();

  if (!config) {
    if (protectedReset) return redirectTo(request, "/auth/error?reason=configuration");
    return NextResponse.next({ request });
  }

  let response = NextResponse.next({ request });
  const supabase = createServerClient(config.url, config.publishableKey, {
    auth: { flowType: "pkce", autoRefreshToken: false, persistSession: true },
    cookieOptions: {
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    },
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, cacheHeaders) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        for (const header of ["cache-control", "expires", "pragma"] as const) {
          const value = cacheHeaders?.[header];
          if (value) response.headers.set(header, value);
        }
      },
    },
  });

  let user = null;
  let verificationFailed = false;
  let accessToken: string | null = null;
  try {
    const result = await supabase.auth.getUser();
    user = result.error ? null : result.data.user;
    verificationFailed = Boolean(result.error?.name === "AuthRetryableFetchError");
    if (user) {
      const sessionResult = await supabase.auth.getSession();
      accessToken = sessionResult.error ? null : sessionResult.data.session?.access_token ?? null;
      if (sessionResult.error?.name === "AuthRetryableFetchError") {
        verificationFailed = true;
      }
    }
  } catch {
    verificationFailed = true;
  }

  if (protectedReset && !user) {
    return redirectTo(request, verificationFailed
      ? "/auth/error?reason=configuration" : "/auth/error?reason=expired-link", response);
  }

  if (protectedReset && user) {
    const recoveryCookie = request.cookies.get(RECOVERY_COOKIE)?.value;
    if (!accessToken || !hasRecoveryProof(recoveryCookie, accessToken)) {
      return redirectTo(request, verificationFailed
        ? "/auth/error?reason=configuration" : "/auth/error?reason=invalid-link", response);
    }
  }

  if (user) response.headers.set("Cache-Control", "private, no-store");
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
