import type { SupabaseClient } from "@supabase/supabase-js";

export type MfaChallengeStatus = "required" | "not-required" | "unavailable";

function isAssuranceLevel(value: unknown): value is "aal1" | "aal2" {
  return value === "aal1" || value === "aal2";
}

export async function getMfaChallengeStatus(client: SupabaseClient): Promise<MfaChallengeStatus> {
  try {
    const { data, error } = await client.auth.mfa.getAuthenticatorAssuranceLevel();
    if (
      error || !data ||
      !isAssuranceLevel(data.currentLevel) ||
      !isAssuranceLevel(data.nextLevel)
    ) return "unavailable";
    return data.nextLevel === "aal2" && data.currentLevel !== "aal2"
      ? "required"
      : "not-required";
  } catch {
    return "unavailable";
  }
}
