export const RECOVERY_COOKIE = "evidence-layer-recovery";
const RECOVERY_PROOF_PREFIX = "evidence-layer-recovery-v1\0";

// This cookie is issued only after Supabase has exchanged a one-time recovery
// link. Binding it to the current access token makes account switches and
// subsequent sign-ins invalidate the recovery state.
export async function recoveryProof(accessToken: string): Promise<string> {
  const input = new TextEncoder().encode(`${RECOVERY_PROOF_PREFIX}${accessToken}`);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function hasRecoveryProof(value: string | undefined, accessToken: string): Promise<boolean> {
  if (!value || !/^[0-9a-f]{64}$/.test(value)) return false;
  const expected = await recoveryProof(accessToken);
  let mismatch = 0;
  for (let index = 0; index < expected.length; index += 1) {
    mismatch |= value.charCodeAt(index) ^ expected.charCodeAt(index);
  }
  return mismatch === 0;
}
