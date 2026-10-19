"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { createSupabaseBrowserClient } from "../lib/supabase/client";
import { getSupabasePublicConfig } from "../lib/supabase/config";
import { safeInternalPath } from "../lib/supabase/redirect";

export type AuthFormMode = "login" | "signup" | "forgot-password" | "reset-password";

const CONTENT: Record<AuthFormMode, { eyebrow: string; title: string; description: string; button: string }> = {
  login: {
    eyebrow: "ACCOUNT ACCESS",
    title: "Sign in",
    description: "Sign in to open your Evidence Layer workspace.",
    button: "Sign in",
  },
  signup: {
    eyebrow: "CREATE AN ACCOUNT",
    title: "Create your account",
    description: "Confirm your email address before using your workspace.",
    button: "Create account",
  },
  "forgot-password": {
    eyebrow: "ACCOUNT RECOVERY",
    title: "Reset your password",
    description: "We will send a reset link if an account matches that address.",
    button: "Send reset link",
  },
  "reset-password": {
    eyebrow: "ACCOUNT RECOVERY",
    title: "Choose a new password",
    description: "Use a new password for your Supabase Auth account.",
    button: "Update password",
  },
};

function confirmationUrl(next: string): string {
  const callback = new URL("/auth/callback", window.location.origin);
  callback.searchParams.set("next", next);
  return callback.toString();
}

export default function AuthForm({
  mode,
  returnTo = "/",
  notice,
}: {
  mode: AuthFormMode;
  returnTo?: string;
  notice?: string;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const content = CONTENT[mode];
  const hasConfiguration = Boolean(getSupabasePublicConfig());

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");

    if (!hasConfiguration) {
      setErrorMessage("Authentication is not configured for this application yet.");
      return;
    }
    if (mode === "signup" && password !== passwordConfirmation) {
      setErrorMessage("The passwords do not match.");
      return;
    }
    if (mode === "reset-password" && password !== passwordConfirmation) {
      setErrorMessage("The passwords do not match.");
      return;
    }

    setPending(true);
    try {
      const supabase = createSupabaseBrowserClient();
      if (mode === "login") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) {
          setErrorMessage("We could not sign you in. Check your details or try again later.");
          return;
        }
        router.replace(safeInternalPath(returnTo));
        router.refresh();
        return;
      }

      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: confirmationUrl("/") },
        });
        if (error) {
          setErrorMessage("We could not create the account. Check the details or try again later.");
          return;
        }
        if (data.session) {
          await supabase.auth.signOut();
          setErrorMessage("Email confirmation is required but is not enabled in the Auth project.");
          return;
        }
        setStatusMessage("Check your email for a confirmation link. You can sign in after confirming your address.");
        return;
      }

      if (mode === "forgot-password") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: confirmationUrl("/reset-password"),
        });
        if (error) {
          setErrorMessage("We could not request a reset email. Try again later.");
          return;
        }
        setStatusMessage("If an account matches that address, a reset link will arrive by email.");
        return;
      }

      const { error } = await supabase.auth.updateUser({ password });
      if (error) {
        setErrorMessage("We could not update the password. The reset link may have expired; request a new one.");
        return;
      }
      setStatusMessage("Password updated. Opening your workspace…");
      router.replace("/");
      router.refresh();
    } catch {
      setErrorMessage("The authentication service could not be reached. Try again later.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <Link className="auth-brand" href="/login" aria-label="Evidence Layer sign in">
          <span className="brand-name">evidence<span>layer</span></span>
        </Link>
        <p className="eyebrow">{content.eyebrow}</p>
        <h1>{content.title}</h1>
        <p className="auth-description">{content.description}</p>

        {notice && <p className="auth-notice" role="status">{notice}</p>}
        {!hasConfiguration && (
          <p className="auth-error" role="alert">
            Authentication is not configured. Set the public Supabase URL and publishable key in the app environment.
          </p>
        )}
        {errorMessage && <p className="auth-error" role="alert">{errorMessage}</p>}
        {statusMessage && <p className="auth-notice" role="status">{statusMessage}</p>}

        <form className="auth-form" onSubmit={submit}>
          {(mode === "login" || mode === "signup" || mode === "forgot-password") && (
            <label>
              <span>Email</span>
              <input
                autoComplete="email"
                autoCapitalize="none"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
          )}
          {(mode === "login" || mode === "signup" || mode === "reset-password") && (
            <label>
              <span>{mode === "reset-password" ? "New password" : "Password"}</span>
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
          )}
          {(mode === "signup" || mode === "reset-password") && (
            <label>
              <span>Confirm password</span>
              <input
                autoComplete="new-password"
                type="password"
                value={passwordConfirmation}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
              />
            </label>
          )}
          <button className="auth-submit" type="submit" disabled={pending || !hasConfiguration}>
            {pending ? "Please wait…" : content.button}
          </button>
        </form>

        <nav className="auth-links" aria-label="Account options">
          {mode === "login" && <Link href="/forgot-password">Forgot password?</Link>}
          {mode === "login" && <Link href="/signup">Create an account</Link>}
          {mode !== "login" && <Link href="/login">Back to sign in</Link>}
        </nav>
      </div>
    </main>
  );
}
