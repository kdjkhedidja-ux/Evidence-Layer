import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import AuthErrorMessage from "../app/auth/error/auth-error-message";

describe("browser-side Supabase callback error fragments", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/auth/error?reason=invalid-link");
  });

  it("identifies a known expired code, hides fragment values, and removes the fragment", async () => {
    window.history.replaceState(
      {},
      "",
      "/auth/error?reason=invalid-link#error=access_denied&error_code=otp_expired&error_description=private-provider-detail&access_token=synthetic-access-token&refresh_token=synthetic-refresh-token",
    );

    render(<AuthErrorMessage initialReason="invalid-link" />);

    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    expect(screen.getByText(/Request a new confirmation or password-reset email/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("private-provider-detail");
    expect(document.body.textContent).not.toContain("synthetic-access-token");
    expect(document.body.textContent).not.toContain("synthetic-refresh-token");
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?reason=invalid-link");
  });

  it("keeps unknown provider errors generic instead of calling them expired", async () => {
    window.history.replaceState(
      {},
      "",
      "/auth/error#error=server_error&error_code=unknown_failure&error_description=private-provider-detail",
    );

    render(<AuthErrorMessage />);

    expect(await screen.findByRole("heading", { name: "This link could not be verified" })).toBeInTheDocument();
    expect(screen.getByText(/may be invalid or already used/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("private-provider-detail");
    expect(window.location.hash).toBe("");
  });

  it("preserves a safe server-provided reason when no fragment is present", async () => {
    window.history.replaceState({}, "", "/auth/error?reason=configuration");

    render(<AuthErrorMessage initialReason="configuration" />);

    expect(screen.getByRole("heading", { name: "Authentication is unavailable" })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?reason=configuration");
  });

  it("treats missing or malformed error details safely and never adopts token fragments as a session", async () => {
    window.history.replaceState({}, "", "/auth/error#%E0%A4%A&access_token=synthetic-token");

    render(<AuthErrorMessage initialReason="unrecognized" />);

    expect(await screen.findByRole("heading", { name: "This link could not be verified" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("synthetic-token");
    expect(window.location.hash).toBe("");
  });
});
