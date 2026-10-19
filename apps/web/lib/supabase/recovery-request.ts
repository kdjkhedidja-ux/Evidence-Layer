import { createClient } from "@supabase/supabase-js";
import { getSupabasePublicConfig, SupabaseConfigurationError } from "./config";

// The default hosted recovery email uses ConfirmationURL. Its redirect must
// carry a session across browsers, so this request alone uses the implicit flow.
export function createSupabaseRecoveryRequestClient() {
  const config = getSupabasePublicConfig();
  if (!config) throw new SupabaseConfigurationError();

  return createClient(config.url, config.publishableKey, {
    auth: {
      flowType: "implicit",
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: false,
    },
  });
}
