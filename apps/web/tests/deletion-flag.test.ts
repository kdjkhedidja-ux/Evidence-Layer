import { describe, expect, it } from "vitest";

import { deletionRequestsEnabled } from "../lib/deletion-flag";

describe("deletionRequestsEnabled", () => {
  it("enables an explicit true flag in any environment", () => {
    expect(deletionRequestsEnabled({ flag: "true", vercelEnv: "preview" })).toBe(true);
  });

  it("keeps an explicit false flag off", () => {
    expect(deletionRequestsEnabled({ flag: "false", vercelEnv: "production" })).toBe(false);
  });

  it("enables production when the flag is unset", () => {
    expect(deletionRequestsEnabled({ vercelEnv: "production" })).toBe(true);
  });

  it("keeps preview and development off when the flag is unset", () => {
    expect(deletionRequestsEnabled({ vercelEnv: "preview" })).toBe(false);
    expect(deletionRequestsEnabled({ vercelEnv: "development" })).toBe(false);
    expect(deletionRequestsEnabled({})).toBe(false);
  });

  it("does not treat an empty flag as unset", () => {
    expect(deletionRequestsEnabled({ flag: "", vercelEnv: "production" })).toBe(false);
  });
});
