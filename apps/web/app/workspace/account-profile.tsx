"use client";

import { useState, type FormEvent } from "react";
import { createSupabaseBrowserClient } from "../../lib/supabase/client";

export default function AccountProfile({ email }: { email: string }) {
  const [newEmail, setNewEmail] = useState("");
  const [emailPending, setEmailPending] = useState(false);
  const [emailMessage, setEmailMessage] = useState("");
  const [emailError, setEmailError] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [reauthNonce, setReauthNonce] = useState("");
  const [nonceRequired, setNonceRequired] = useState(false);
  const [passwordPending, setPasswordPending] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState("");
  const [passwordError, setPasswordError] = useState("");

  async function changeEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = newEmail.trim();
    if (!candidate || candidate.toLowerCase() === email.toLowerCase()) {
      setEmailError("Enter a different email address.");
      return;
    }

    setEmailPending(true);
    setEmailMessage("");
    setEmailError("");
    try {
      const { error } = await createSupabaseBrowserClient().auth.updateUser({ email: candidate });
      if (error) {
        setEmailError("The email change could not be started. Check the address and try again.");
        return;
      }
      setEmailMessage("Confirmation instructions were sent. The account email changes after confirmation.");
      setNewEmail("");
    } catch {
      setEmailError("The email change could not be started. Try again later.");
    } finally {
      setEmailPending(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPasswordMessage("");
    setPasswordError("");
    if (newPassword.length < 12) {
      setPasswordError("Use a password with at least 12 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("The new passwords do not match.");
      return;
    }

    setPasswordPending(true);
    try {
      const supabase = createSupabaseBrowserClient();
      const attributes = {
        current_password: currentPassword,
        password: newPassword,
        ...(nonceRequired && reauthNonce.trim() ? { nonce: reauthNonce.trim() } : {}),
      };
      const { error } = await supabase.auth.updateUser(attributes);
      if (error) {
        const code = typeof error.code === "string" ? error.code.toLowerCase() : "";
        const needsReauthentication =
          code === "reauthentication_needed" ||
          code === "reauthentication_required" ||
          error.message.toLowerCase().includes("reauthentication");
        if (needsReauthentication && !nonceRequired) {
          const { error: reauthError } = await supabase.auth.reauthenticate();
          if (!reauthError) {
            setNonceRequired(true);
            setPasswordMessage("A verification code was sent to your account email. Enter it below and submit again.");
            return;
          }
        }
        setPasswordError("The password could not be changed. Verify your current password and try again.");
        return;
      }

      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setReauthNonce("");
      setNonceRequired(false);
      setPasswordMessage("Password changed successfully.");
    } catch {
      setPasswordError("The password could not be changed. Try again later.");
    } finally {
      setPasswordPending(false);
    }
  }

  return (
    <section id="account-profile" className="workspace-panel account-profile" aria-labelledby="account-profile-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">Account settings</p>
          <h2 id="account-profile-heading">Account profile</h2>
        </div>
      </div>

      <form className="workspace-form" onSubmit={changeEmail}>
        <h3>Change email</h3>
        <label htmlFor="profile-new-email">New email address</label>
        <input
          id="profile-new-email"
          type="email"
          autoComplete="email"
          value={newEmail}
          onChange={(event) => setNewEmail(event.target.value)}
          required
          disabled={emailPending}
        />
        <button type="submit" disabled={emailPending}>
          {emailPending ? "Sending confirmation…" : "Change email"}
        </button>
        {emailMessage && <p className="workspace-message" role="status">{emailMessage}</p>}
        {emailError && <p className="workspace-message" role="alert">{emailError}</p>}
      </form>

      <form className="workspace-form" onSubmit={changePassword}>
        <h3>Change password</h3>
        <p className="workspace-note">
          Passwords must be at least 12 characters. A current password is required; if the session
          is older than 24 hours, Supabase will email a one-time reauthentication code.
        </p>
        <label htmlFor="profile-current-password">Current password</label>
        <input
          id="profile-current-password"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
          required
          disabled={passwordPending}
        />
        <label htmlFor="profile-new-password">New password</label>
        <input
          id="profile-new-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
          required
          disabled={passwordPending}
        />
        <label htmlFor="profile-confirm-password">Confirm new password</label>
        <input
          id="profile-confirm-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          required
          disabled={passwordPending}
        />
        {nonceRequired && (
          <>
            <label htmlFor="profile-reauth-code">Email verification code</label>
            <input
              id="profile-reauth-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={reauthNonce}
              onChange={(event) => setReauthNonce(event.target.value)}
              required
              disabled={passwordPending}
            />
          </>
        )}
        <button type="submit" disabled={passwordPending}>
          {passwordPending ? "Changing password…" : "Change password"}
        </button>
        {passwordMessage && <p className="workspace-message" role="status">{passwordMessage}</p>}
        {passwordError && <p className="workspace-message" role="alert">{passwordError}</p>}
      </form>
    </section>
  );
}
