import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getSupabasePublicConfig, SupabaseConfigurationError } from "./config";

let browserClient: SupabaseClient | null = null;

export function createSupabaseBrowserClient(): SupabaseClient {
  if (browserClient) return browserClient;
  const config = getSupabasePublicConfig();
  if (!config) throw new SupabaseConfigurationError();

  browserClient = createBrowserClient(config.url, config.publishableKey, {
    auth: { flowType: "pkce", autoRefreshToken: false, persistSession: true },
    cookieOptions: {
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    },
  });
  return browserClient;
}
