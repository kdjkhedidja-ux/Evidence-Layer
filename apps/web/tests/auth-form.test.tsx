import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
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
    expect(fetch).toHaveBeenCalledWith("/api/evidence/personal-workspace", { method: "PUT", cache: "no-store" });
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

  it("prevents duplicate sign-in submissions while Supabase is pending", async () => {
    let completeSignIn!: (result: { error: null }) => void;
    mocks.auth.signInWithPassword.mockImplementation(() => new Promise((resolve) => {
      completeSignIn = resolve;
    }));
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);
    await user.type(screen.getByLabelText("Email"), "user@example.test");
    await user.type(screen.getByLabelText("Password"), "synthetic-password");
    const form = screen.getByRole("button", { name: "Sign in" }).closest("form");
    if (!form) throw new Error("Sign-in form was not rendered");

    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(mocks.auth.signInWithPassword).toHaveBeenCalledOnce();
    completeSignIn({ error: null });
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/"));
  });

  it("holds the signed-in account when workspace provisioning fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);
    await user.type(screen.getByLabelText("Email"), "user@example.test");
    await user.type(screen.getByLabelText("Password"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("workspace could not be opened");
    expect(mocks.replace).not.toHaveBeenCalled();
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

  it("reports when a session from misconfigured signup could not be cleared", async () => {
    mocks.auth.signUp.mockResolvedValue({
      data: { user: { id: "synthetic-user" }, session: { access_token: "synthetic-only" } },
      error: null,
    });
    mocks.auth.signOut.mockResolvedValueOnce({ error: { message: "private sign-out detail" } });
    const user = userEvent.setup();
    render(<AuthForm mode="signup" />);
    await user.type(screen.getByLabelText("Email"), "new@example.test");
    await user.type(screen.getByLabelText("Password", { selector: "input" }), "synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "synthetic-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("session could not be cleared");
    expect(screen.queryByText("private sign-out detail")).not.toBeInTheDocument();
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

  it("submits the new password only through the guarded recovery endpoint", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="reset-password" />);
    await user.type(screen.getByLabelText("New password"), "new-synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "new-synthetic-password");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/auth/reset-password", expect.objectContaining({
      method: "POST", body: JSON.stringify({ password: "new-synthetic-password" }),
    })));
    expect(mocks.auth.updateUser).not.toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("Password updated");
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("button", { name: "Update password" })).not.toBeInTheDocument();
  });

  it("reports an expired recovery session without leaking response details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private", { status: 403 })));
    const user = userEvent.setup();
    render(<AuthForm mode="reset-password" />);
    await user.type(screen.getByLabelText("New password"), "new-synthetic-password");
    await user.type(screen.getByLabelText("Confirm password"), "new-synthetic-password");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or expired");
    expect(screen.queryByText("private")).not.toBeInTheDocument();
  });

  it("fails closed when public Supabase configuration is absent", async () => {
    mocks.getConfig.mockReturnValue(null);
    render(<AuthForm mode="login" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Authentication is not configured");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });
});
