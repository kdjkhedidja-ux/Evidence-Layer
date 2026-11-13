import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getMfaChallengeStatus } from "../lib/supabase/mfa";

function clientWithLevel(data: unknown, error: unknown = null): SupabaseClient {
  return {
    auth: {
      mfa: {
        getAuthenticatorAssuranceLevel: vi.fn(async () => ({ data, error })),
      },
    },
  } as unknown as SupabaseClient;
}

describe("MFA challenge status", () => {
  it("requires a challenge when an enrolled factor leaves the session at aal1", async () => {
    expect(await getMfaChallengeStatus(clientWithLevel({ currentLevel: "aal1", nextLevel: "aal2" }))).toBe("required");
  });

  it("accepts sessions that already completed the challenge", async () => {
    expect(await getMfaChallengeStatus(clientWithLevel({ currentLevel: "aal2", nextLevel: "aal2" }))).toBe("not-required");
  });

  it("does not require a challenge when no factor is enrolled", async () => {
    expect(await getMfaChallengeStatus(clientWithLevel({ currentLevel: "aal1", nextLevel: "aal1" }))).toBe("not-required");
  });

  it("reports lookup errors or exceptions as unavailable", async () => {
    expect(await getMfaChallengeStatus(clientWithLevel(null, { message: "unavailable" }))).toBe("unavailable");
    const throwing = {
      auth: {
        mfa: {
          getAuthenticatorAssuranceLevel: vi.fn(async () => {
            throw new Error("network");
          }),
        },
      },
    } as unknown as SupabaseClient;
    expect(await getMfaChallengeStatus(throwing)).toBe("unavailable");
  });

  it("fails closed when the assurance response is incomplete", async () => {
    expect(await getMfaChallengeStatus(clientWithLevel({ currentLevel: "aal1" }))).toBe("unavailable");
    expect(await getMfaChallengeStatus(clientWithLevel({ currentLevel: "unknown", nextLevel: "aal1" }))).toBe("unavailable");
  });
});
