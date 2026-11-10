import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * Returns true when the verified session has an enrolled second factor but has
 * not completed the challenge yet. Errors fail open: identity has already been
 * verified server-side, and a transient AAL lookup must not lock the account.
 */
export async function mfaChallengeRequired(client: SupabaseClient): Promise<boolean> {
  try {
    const { data, error } = await client.auth.mfa.getAuthenticatorAssuranceLevel();
    if (error || !data) return false;
    return data.nextLevel === "aal2" && data.currentLevel !== "aal2";
  } catch {
    return false;
  }
}
