import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { mfaChallengeRequired } from "../lib/supabase/mfa";

function clientWithLevel(data: unknown, error: unknown = null): SupabaseClient {
  return {
    auth: {
      mfa: {
        getAuthenticatorAssuranceLevel: vi.fn(async () => ({ data, error })),
      },
    },
  } as unknown as SupabaseClient;
}

describe("MFA challenge requirement", () => {
  it("requires a challenge when an enrolled factor leaves the session at aal1", async () => {
    expect(await mfaChallengeRequired(clientWithLevel({ currentLevel: "aal1", nextLevel: "aal2" }))).toBe(true);
  });

  it("accepts sessions that already completed the challenge", async () => {
    expect(await mfaChallengeRequired(clientWithLevel({ currentLevel: "aal2", nextLevel: "aal2" }))).toBe(false);
  });

  it("does not require a challenge when no factor is enrolled", async () => {
    expect(await mfaChallengeRequired(clientWithLevel({ currentLevel: "aal1", nextLevel: "aal1" }))).toBe(false);
  });

  it("fails open on lookup errors or exceptions", async () => {
    expect(await mfaChallengeRequired(clientWithLevel(null, { message: "unavailable" }))).toBe(false);
    const throwing = {
      auth: {
        mfa: {
          getAuthenticatorAssuranceLevel: vi.fn(async () => {
            throw new Error("network");
          }),
        },
      },
    } as unknown as SupabaseClient;
    expect(await mfaChallengeRequired(throwing)).toBe(false);
  });
});
