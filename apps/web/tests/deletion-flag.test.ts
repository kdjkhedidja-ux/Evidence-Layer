import { describe, expect, it } from "vitest";

import { deletionRequestsEnabled } from "../lib/deletion-flag";

describe("deletionRequestsEnabled", () => {
  it("enables the deletion UI when the flag is unset", () => {
    expect(deletionRequestsEnabled({})).toBe(true);
  });

  it("enables an explicit true flag", () => {
    expect(deletionRequestsEnabled({ flag: "true" })).toBe(true);
  });

  it("keeps an explicit false flag off", () => {
    expect(deletionRequestsEnabled({ flag: "false" })).toBe(false);
  });

  it("treats any other value as enabled", () => {
    expect(deletionRequestsEnabled({ flag: "" })).toBe(true);
    expect(deletionRequestsEnabled({ flag: "TRUE" })).toBe(true);
  });
});
