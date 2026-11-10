import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MfaPanel from "../app/mfa/mfa-panel";

const mocks = vi.hoisted(() => ({
  router: { replace: vi.fn(), refresh: vi.fn() },
  listFactors: vi.fn(),
  assurance: vi.fn(),
  challenge: vi.fn(),
  verify: vi.fn(),
  enroll: vi.fn(),
  unenroll: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: {
      mfa: {
        listFactors: mocks.listFactors,
        getAuthenticatorAssuranceLevel: mocks.assurance,
        challenge: mocks.challenge,
        verify: mocks.verify,
        enroll: mocks.enroll,
        unenroll: mocks.unenroll,
      },
    },
  }),
}));

const VERIFIED_FACTOR = { id: "factor-1", status: "verified", friendly_name: "Phone" };

describe("MFA panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.assurance.mockResolvedValue({
      data: { currentLevel: "aal1", nextLevel: "aal2" },
      error: null,
    });
    mocks.listFactors.mockResolvedValue({
      data: { all: [VERIFIED_FACTOR], totp: [VERIFIED_FACTOR] },
      error: null,
    });
    mocks.challenge.mockResolvedValue({ data: { id: "challenge-1" }, error: null });
    mocks.verify.mockResolvedValue({ data: {}, error: null });
  });

  it("challenges an enrolled factor and continues to the requested page", async () => {
    render(<MfaPanel next="/workspace" />);
    const input = await screen.findByLabelText("Authenticator code");
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => {
      expect(mocks.verify).toHaveBeenCalledWith({
        factorId: "factor-1",
        challengeId: "challenge-1",
        code: "123456",
      });
    });
    expect(mocks.router.replace).toHaveBeenCalledWith("/workspace");
  });

  it("offers enrollment when no authenticator is enrolled", async () => {
    mocks.assurance.mockResolvedValue({
      data: { currentLevel: "aal1", nextLevel: "aal1" },
      error: null,
    });
    mocks.listFactors.mockResolvedValue({ data: { all: [], totp: [] }, error: null });
    mocks.enroll.mockResolvedValue({
      data: {
        id: "factor-new",
        totp: { qr_code: "data:image/svg+xml;utf-8,<svg/>", secret: "ABCDEF" },
      },
      error: null,
    });
    render(<MfaPanel next="/workspace" />);
    fireEvent.click(await screen.findByRole("button", { name: "Set up authenticator" }));
    await screen.findByAltText("Authenticator enrollment QR code");
    expect(screen.getByText("ABCDEF")).toBeInTheDocument();
    expect(mocks.enroll).toHaveBeenCalledWith({
      factorType: "totp",
      friendlyName: "Evidence Layer authenticator",
    });
  });
});
