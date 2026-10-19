import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const script = readFileSync(resolve(process.cwd(), "public/recovery-complete.js"), "utf8");

function run(hash: string) {
  const replace = vi.fn();
  const replaceState = vi.fn();
  const fetch = vi.fn().mockResolvedValue({ ok: true });
  const window = {
    location: { hash, pathname: "/auth/callback", search: "?next=%2Freset-password", replace },
    history: { state: null, replaceState },
  };
  runInNewContext(script, { window, fetch, URLSearchParams });
  return { fetch, replace, replaceState };
}

describe("implicit recovery fragment handoff", () => {
  it("clears the fragment and sends tokens only to the same-origin recovery API", async () => {
    const result = run("#access_token=synthetic-access&refresh_token=synthetic-refresh&type=recovery");
    expect(result.replaceState).toHaveBeenCalledWith(null, "", "/auth/callback?next=%2Freset-password");
    expect(result.fetch).toHaveBeenCalledWith("/api/auth/recovery-session", expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
      referrerPolicy: "no-referrer",
      body: JSON.stringify({ accessToken: "synthetic-access", refreshToken: "synthetic-refresh" }),
    }));
    await vi.waitFor(() => expect(result.replace).toHaveBeenCalledWith("/reset-password"));
  });

  it("rejects fragments that are not one recovery session", () => {
    const result = run("#access_token=synthetic-access&refresh_token=synthetic-refresh&type=signup");
    expect(result.fetch).not.toHaveBeenCalled();
    expect(result.replace).toHaveBeenCalledWith("/auth/error?reason=invalid-link");
  });
});
