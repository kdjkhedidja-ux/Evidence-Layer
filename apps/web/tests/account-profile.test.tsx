import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountProfile from "../app/workspace/account-profile";

const mocks = vi.hoisted(() => ({
  updateUser: vi.fn(),
  reauthenticate: vi.fn(),
}));

vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: {
      updateUser: mocks.updateUser,
      reauthenticate: mocks.reauthenticate,
    },
  }),
}));

function passwordForm(): HTMLFormElement {
  const form = screen.getByLabelText("Current password").closest("form");
  if (!(form instanceof HTMLFormElement)) throw new Error("Password form was not rendered");
  return form;
}

describe("Account profile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.updateUser.mockResolvedValue({ error: null });
    mocks.reauthenticate.mockResolvedValue({ error: null });
  });

  it("starts an email change and tells the user to confirm it", async () => {
    const user = userEvent.setup();
    render(<AccountProfile email="current@example.test" />);
    await user.type(screen.getByLabelText("New email address"), "next@example.test");
    await user.click(screen.getByRole("button", { name: "Change email" }));

    await screen.findByText(/Confirmation instructions were sent/);
    expect(mocks.updateUser).toHaveBeenCalledWith({ email: "next@example.test" });
  });

  it("changes a password with current-password validation", async () => {
    const user = userEvent.setup();
    render(<AccountProfile email="current@example.test" />);
    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText("New password"), "new-password-2026");
    await user.type(screen.getByLabelText("Confirm new password"), "new-password-2026");
    await user.click(within(passwordForm()).getByRole("button", { name: "Change password" }));

    await screen.findByText("Password changed successfully.");
    expect(mocks.updateUser).toHaveBeenCalledWith({
      current_password: "old-password",
      password: "new-password-2026",
    });
  });

  it("requests and submits a reauthentication nonce for an old session", async () => {
    mocks.updateUser
      .mockResolvedValueOnce({
        error: { code: "reauthentication_needed", message: "reauthentication needed" },
      })
      .mockResolvedValueOnce({ error: null });
    const user = userEvent.setup();
    render(<AccountProfile email="current@example.test" />);
    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText("New password"), "new-password-2026");
    await user.type(screen.getByLabelText("Confirm new password"), "new-password-2026");
    await user.click(within(passwordForm()).getByRole("button", { name: "Change password" }));

    await screen.findByLabelText("Email verification code");
    expect(mocks.reauthenticate).toHaveBeenCalledOnce();
    await user.type(screen.getByLabelText("Email verification code"), "123456");
    await user.click(within(passwordForm()).getByRole("button", { name: "Change password" }));

    await screen.findByText("Password changed successfully.");
    expect(mocks.updateUser).toHaveBeenLastCalledWith({
      current_password: "old-password",
      password: "new-password-2026",
      nonce: "123456",
    });
  });

  it("rejects mismatched passwords before contacting Supabase", async () => {
    render(<AccountProfile email="current@example.test" />);
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "old" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password-2026" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different-password" } });
    fireEvent.submit(passwordForm());

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("do not match"));
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });
});
