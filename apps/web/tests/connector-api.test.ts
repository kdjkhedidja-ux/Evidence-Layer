import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET as listNotIndexed } from "../app/api/evidence/connectors/[connectionId]/not-indexed/route";
import { POST as syncConnector } from "../app/api/evidence/connectors/[connectionId]/sync/route";

const mocks = vi.hoisted(() => ({
  getAuth: vi.fn(),
  serviceUrl: vi.fn(),
}));

vi.mock("../lib/supabase/server", () => ({
  getVerifiedAuthContext: () => mocks.getAuth(),
}));
vi.mock("../lib/supabase/config", () => ({
  getEvidenceServiceBaseUrl: () => mocks.serviceUrl(),
}));

const CONNECTION_ID = "10000000-0000-4000-8000-000000000001";

describe("connector sync proxy", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue({
      kind: "authenticated",
      accessToken: "synthetic-token",
      client: {
        auth: {
          mfa: {
            getAuthenticatorAssuranceLevel: async () => ({
              data: { currentLevel: "aal1", nextLevel: "aal1" },
              error: null,
            }),
          },
        },
      },
    });
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      connection_id: CONNECTION_ID,
      listed: 25,
      ingested: 25,
      skipped: 0,
      next_cursor: null,
    })));
  });

  it("requests the documented maximum of 25 items", async () => {
    const response = await syncConnector(
      new Request(`https://app.example/api/evidence/connectors/${CONNECTION_ID}/sync`, {
        method: "POST",
        headers: { Origin: "https://app.example" },
      }),
      { params: Promise.resolve({ connectionId: CONNECTION_ID }) },
    );

    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(
      `https://evidence.example/v1/connectors/${CONNECTION_ID}/sync?limit=25`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("lists the files of a source that are recorded but not indexed", async () => {
    const rows = [{ repository: "acme/docs", path: "data/huge.json", reason: "too_large", size_bytes: 3145728 }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(rows)));

    const response = await listNotIndexed(
      new Request(`https://app.example/api/evidence/connectors/${CONNECTION_ID}/not-indexed`),
      { params: Promise.resolve({ connectionId: CONNECTION_ID }) },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(await response.json()).toEqual(rows);
    expect(fetch).toHaveBeenCalledWith(
      `https://evidence.example/v1/connectors/${CONNECTION_ID}/not-indexed`,
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer synthetic-token" }),
      }),
    );
  });

  it("rejects a malformed connection id and an anonymous caller", async () => {
    const malformed = await listNotIndexed(
      new Request("https://app.example/api/evidence/connectors/x/not-indexed"),
      { params: Promise.resolve({ connectionId: "../me" }) },
    );
    expect(malformed.status).toBe(404);

    mocks.getAuth.mockResolvedValue({ kind: "unauthenticated" });
    const anonymous = await listNotIndexed(
      new Request(`https://app.example/api/evidence/connectors/${CONNECTION_ID}/not-indexed`),
      { params: Promise.resolve({ connectionId: CONNECTION_ID }) },
    );
    expect(anonymous.status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
});
