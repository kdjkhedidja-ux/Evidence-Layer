import { describe, expect, it } from "vitest";

import { resolveNotionRedirectUri } from "../lib/notion-oauth";

const requestUrl = "https://evidencelayer.dev/workspace";

describe("resolveNotionRedirectUri", () => {
  it("falls back to the same-origin callback", () => {
    expect(resolveNotionRedirectUri({ requestUrl })).toBe(
      "https://evidencelayer.dev/api/connectors/notion/callback",
    );
  });

  it("accepts a valid callback", () => {
    expect(
      resolveNotionRedirectUri({
        configured: "https://evidencelayer.dev/api/connectors/notion/callback",
        requestUrl,
      }),
    ).toBe("https://evidencelayer.dev/api/connectors/notion/callback");
  });

  it("rejects the Notion authorization URL", () => {
    expect(
      resolveNotionRedirectUri({
        configured: "https://api.notion.com/v1/oauth/authorize",
        requestUrl,
      }),
    ).toBeNull();
  });

  it("rejects a non-callback path", () => {
    expect(
      resolveNotionRedirectUri({
        configured: "https://evidencelayer.dev/api/connectors/notion/start",
        requestUrl,
      }),
    ).toBeNull();
  });
});
