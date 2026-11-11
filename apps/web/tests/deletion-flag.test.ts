import { describe, expect, it } from "vitest";

import { deletionRequestsEnabled } from "../lib/deletion-flag";

describe("deletionRequestsEnabled", () => {
  it("keeps the deletion UI off when the flag is unset", () => {
    expect(deletionRequestsEnabled({})).toBe(false);
  });

  it("enables only an explicit true flag", () => {
    expect(deletionRequestsEnabled({ flag: "true" })).toBe(true);
  });

  it("keeps an explicit false flag off", () => {
    expect(deletionRequestsEnabled({ flag: "false" })).toBe(false);
  });

  it("treats any other value as off", () => {
    expect(deletionRequestsEnabled({ flag: "" })).toBe(false);
    expect(deletionRequestsEnabled({ flag: "TRUE" })).toBe(false);
    expect(deletionRequestsEnabled({ flag: "1" })).toBe(false);
  });
});
