import { createHash, timingSafeEqual } from "node:crypto";

export const RECOVERY_COOKIE = "evidence-layer-recovery";

// This cookie is issued only after Supabase has exchanged a one-time recovery
// link. Binding it to the current access token makes account switches and
// subsequent sign-ins invalidate the recovery state.
export function recoveryProof(accessToken: string): string {
  return createHash("sha256")
    .update("evidence-layer-recovery-v1\0")
    .update(accessToken)
    .digest("hex");
}

export function hasRecoveryProof(value: string | undefined, accessToken: string): boolean {
  if (!value || !/^[0-9a-f]{64}$/.test(value)) return false;
  const expected = Buffer.from(recoveryProof(accessToken), "hex");
  return timingSafeEqual(Buffer.from(value, "hex"), expected);
}
