import { describe, expect, it } from "vitest";
import { authCallbackUrl, safeInternalPath } from "../lib/supabase/redirect";

describe("authentication redirect URLs", () => {
  it.each([
    ["http://localhost:3000", "http://localhost:3000/auth/callback"],
    ["https://evidence-layer.vercel.app", "https://evidence-layer.vercel.app/auth/callback"],
  ])("keeps recovery on the current application origin %s", (origin, expectedCallback) => {
    const url = new URL(authCallbackUrl(origin, "/reset-password"));
    expect(`${url.origin}${url.pathname}`).toBe(expectedCallback);
    expect(url.searchParams.get("next")).toBe("/reset-password");
  });

  it.each([
    "https://outside.example/path",
    "//outside.example/path",
    "/%5coutside.example/path",
    "/\nhttps://outside.example",
  ])("rejects an unsafe next destination", (next) => {
    expect(safeInternalPath(next)).toBe("/");
  });

  it("keeps an internal destination and its fragment", () => {
    expect(safeInternalPath("/workspace?tab=mine#details")).toBe("/workspace?tab=mine#details");
  });
});
