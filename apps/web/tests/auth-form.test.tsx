import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthForm from "../components/auth-form";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
  getConfig: vi.fn(),
  auth: {
    signInWithPassword: vi.fn(),
    signUp: vi.fn(),
    resetPasswordForEmail: vi.fn(),
    updateUser: vi.fn(),
    signOut: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }),
}));
vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({ auth: mocks.auth }),
}));
vi.mock("../lib/supabase/config", () => ({
  getSupabasePublicConfig: () => mocks.getConfig(),
}));

describe("Supabase Auth forms", () => {
  beforeEach(() => {
    mocks.getConfig.mockReturnValue({
      url: "https://sample-project.supabase.co",
      publishableKey: "sb_publishable_synthetic_test_key",
    });
    mocks.auth.signInWithPassword.mockResolvedValue({ error: null });
    mocks.auth.signUp.mockResolvedValue({ data: { user: { id: "synthetic-user" }, session: null }, error: null });
    mocks.auth.resetPasswordForEmail.mockResolvedValue({ error: null });
    mocks.auth.updateUser.mockResolvedValue({ error: null });
    mocks.auth.signOut.mockResolvedValue({ error: null });
  });

  it("signs in through Supabase and uses only a safe internal return path", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="login" returnTo="//outside.example" />);
    await user.type(screen.getByLabelText("Email"), "user@example.test");
    await user.type(screen.getByLabelText("Password"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/"));
    expect(mocks.auth.signInWithPassword).toHaveBeenCalledWith({
      email: "user@example.test",
      password: "synthetic-password",
    });
    expect(mocks.refresh).toHaveBeenCalledOnce();
  });

  it("shows a sanitized sign-in error", async () => {
    mocks.auth.signInWithPassword.mockResolvedValue({ error: { message: "raw provider detail" } });
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);
    await user.type(screen.getByLabelText("Email"), "user@example.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("We could not sign you in");
    expect(screen.queryByText(/raw provider detail/)).not.toBeInTheDocument();
  });

  it("requires matching signup passwords and requests email confirmation", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="signup" />);
    await user.type(screen.getByLabelText("Email"), "new@example.test");
    await user.type(screen.getByLabelText("Password", { selector: "input" }), "synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "different-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("passwords do not match");
    expect(mocks.auth.signUp).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Confirm password"));
    await user.type(screen.getByLabelText("Confirm password"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Check your email for a confirmation link");
    const credentials = mocks.auth.signUp.mock.calls[0][0];
    expect(credentials.options.emailRedirectTo).toContain("/auth/callback");
    expect(credentials.options.emailRedirectTo).toContain("next=%2F");
  });

  it("does not keep a session when email confirmation appears disabled", async () => {
    mocks.auth.signUp.mockResolvedValue({
      data: { user: { id: "synthetic-user" }, session: { access_token: "synthetic-only" } },
      error: null,
    });
    const user = userEvent.setup();
    render(<AuthForm mode="signup" />);
    await user.type(screen.getByLabelText("Email"), "new@example.test");
    await user.type(screen.getByLabelText("Password", { selector: "input" }), "synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(mocks.auth.signOut).toHaveBeenCalledOnce();
    expect(await screen.findByRole("alert")).toHaveTextContent("Email confirmation is required");
  });

  it("keeps password reset requests non-enumerating", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="forgot-password" />);
    await user.type(screen.getByLabelText("Email"), "user@example.test");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If an account matches that address");
    expect(mocks.auth.resetPasswordForEmail).toHaveBeenCalledWith(
      "user@example.test",
      expect.objectContaining({ redirectTo: expect.stringContaining("/auth/callback") }),
    );
    expect(mocks.auth.resetPasswordForEmail.mock.calls[0][1].redirectTo).toContain("next=%2Freset-password");
  });

  it("updates the signed-in account password after a verified callback", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="reset-password" />);
    await user.type(screen.getByLabelText("New password"), "new-synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "new-synthetic-password");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() => expect(mocks.auth.updateUser).toHaveBeenCalledWith({ password: "new-synthetic-password" }));
    expect(mocks.replace).toHaveBeenCalledWith("/");
  });

  it("fails closed when public Supabase configuration is absent", async () => {
    mocks.getConfig.mockReturnValue(null);
    render(<AuthForm mode="login" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Authentication is not configured");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });
});
