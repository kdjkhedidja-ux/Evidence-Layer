import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { getSupabasePublicConfig } from "./lib/supabase/config";

function redirectTo(request: NextRequest, path: string): NextResponse {
  return NextResponse.redirect(new URL(path, request.url));
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const pathname = request.nextUrl.pathname;
  const protectedExplorer = pathname === "/";
  const protectedReset = pathname === "/reset-password";
  const config = getSupabasePublicConfig();

  if (!config) {
    if (protectedExplorer) return redirectTo(request, "/login?reason=configuration");
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
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  let user = null;
  let verificationFailed = false;
  try {
    const result = await supabase.auth.getUser();
    user = result.data.user;
    verificationFailed = Boolean(result.error?.name === "AuthRetryableFetchError");
  } catch {
    verificationFailed = true;
  }

  if (protectedExplorer && !user) {
    return redirectTo(
      request,
      verificationFailed ? "/login?reason=session-check" : "/login?reason=sign-in",
    );
  }
  if (protectedReset && !user) {
    return redirectTo(request, "/auth/error?reason=expired-link");
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
