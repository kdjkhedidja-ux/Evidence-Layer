import { describe, expect, it } from "vitest";

import { googleDriveConnectorEnabled } from "../lib/connector-flags";

describe("googleDriveConnectorEnabled", () => {
  it("defaults to off", () => {
    expect(googleDriveConnectorEnabled({})).toBe(false);
    expect(googleDriveConnectorEnabled({ flag: "false" })).toBe(false);
    expect(googleDriveConnectorEnabled({ flag: "TRUE" })).toBe(false);
  });

  it("requires exactly true", () => {
    expect(googleDriveConnectorEnabled({ flag: "true" })).toBe(true);
  });
});
