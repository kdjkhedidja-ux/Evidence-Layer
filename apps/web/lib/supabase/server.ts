import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient, User } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import { getSupabasePublicConfig, SupabaseConfigurationError } from "./config";

export type VerifiedAuthContext =
  | { kind: "authenticated"; client: SupabaseClient; user: User; accessToken: string }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

export async function createSupabaseServerClient(): Promise<SupabaseClient> {
  const config = getSupabasePublicConfig();
  if (!config) throw new SupabaseConfigurationError();
  const cookieStore = await cookies();

  return createServerClient(config.url, config.publishableKey, {
    auth: { flowType: "pkce", autoRefreshToken: false, persistSession: true },
    cookieOptions: {
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    },
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Components cannot write cookies; middleware refreshes sessions.
        }
      },
    },
  });
}

export async function getVerifiedAuthContext(): Promise<VerifiedAuthContext> {
  let client: SupabaseClient;
  try {
    client = await createSupabaseServerClient();
  } catch (error) {
    if (error instanceof SupabaseConfigurationError) return { kind: "unavailable" };
    return { kind: "unavailable" };
  }

  try {
    const { data: userData, error: userError } = await client.auth.getUser();
    if (userError?.name === "AuthRetryableFetchError") return { kind: "unavailable" };
    if (userError || !userData.user) return { kind: "unauthenticated" };

    // getSession reads the cookie; it is used only to retrieve the token after
    // getUser has verified the server-side identity.
    const { data: sessionData, error: sessionError } = await client.auth.getSession();
    if (sessionError?.name === "AuthRetryableFetchError") return { kind: "unavailable" };
    const session = sessionData.session;
    if (!session?.access_token || session.user.id !== userData.user.id) {
      return { kind: "unauthenticated" };
    }

    return {
      kind: "authenticated",
      client,
      user: userData.user,
      accessToken: session.access_token,
    };
  } catch {
    return { kind: "unavailable" };
  }
}
