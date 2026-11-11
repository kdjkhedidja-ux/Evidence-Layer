import { describe, expect, it } from "vitest";

import { accountExportEnabled } from "../lib/export-flag";

describe("accountExportEnabled", () => {
  it("is off when unset", () => {
    expect(accountExportEnabled({})).toBe(false);
  });

  it("requires exactly true", () => {
    expect(accountExportEnabled({ flag: "true" })).toBe(true);
    expect(accountExportEnabled({ flag: "TRUE" })).toBe(false);
    expect(accountExportEnabled({ flag: "false" })).toBe(false);
  });
});
