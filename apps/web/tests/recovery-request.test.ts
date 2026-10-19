import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSupabaseRecoveryRequestClient } from "../lib/supabase/recovery-request";

const mocks = vi.hoisted(() => ({ createClient: vi.fn(), getConfig: vi.fn() }));

vi.mock("@supabase/supabase-js", () => ({ createClient: mocks.createClient }));
vi.mock("../lib/supabase/config", () => ({
  getSupabasePublicConfig: mocks.getConfig,
  SupabaseConfigurationError: class extends Error {},
}));

describe("isolated recovery request client", () => {
  beforeEach(() => {
    mocks.getConfig.mockReturnValue({
      url: "https://sample.supabase.co",
      publishableKey: "sb_publishable_synthetic",
    });
  });

  it("requests a cross-browser implicit link without persisting another session", () => {
    createSupabaseRecoveryRequestClient();
    expect(mocks.createClient).toHaveBeenCalledWith(
      "https://sample.supabase.co",
      "sb_publishable_synthetic",
      { auth: {
        flowType: "implicit",
        autoRefreshToken: false,
        detectSessionInUrl: false,
        persistSession: false,
      } },
    );
  });
});
