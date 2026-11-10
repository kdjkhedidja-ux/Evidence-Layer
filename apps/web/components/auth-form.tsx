"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { createSupabaseBrowserClient } from "../lib/supabase/client";
import { createSupabaseRecoveryRequestClient } from "../lib/supabase/recovery-request";
import { getSupabasePublicConfig } from "../lib/supabase/config";
import { authCallbackUrl, safeInternalPath } from "../lib/supabase/redirect";

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
  const submitting = useRef(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [completed, setCompleted] = useState(false);
  const [sessionEstablished, setSessionEstablished] = useState(false);
  const content = CONTENT[mode];
  const hasConfiguration = Boolean(getSupabasePublicConfig());

  function mfaTarget(): string {
    return `/mfa?next=${encodeURIComponent(safeInternalPath(returnTo))}`;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || completed) return;
    setErrorMessage("");
    setStatusMessage("");

    if (!hasConfiguration) {
      setErrorMessage("Authentication is not configured for this application yet.");
      return;
    }
    const normalizedEmail = email.trim();
    if (mode !== "reset-password" && !(mode === "login" && sessionEstablished) &&
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setErrorMessage("Enter a valid email address.");
      return;
    }
    if (mode !== "login" && mode !== "forgot-password" && password.length < 12) {
      setErrorMessage("Use a password of at least 12 characters.");
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

    submitting.current = true;
    setPending(true);
    try {
      const supabase = createSupabaseBrowserClient();
      if (mode === "login") {
        if (!sessionEstablished) {
          const { data, error } = await supabase.auth.signInWithPassword({ email: normalizedEmail, password });
          if (error || !data?.session) {
            setErrorMessage("We could not sign you in. Check your details or try again later.");
            return;
          }
          setSessionEstablished(true);
        }
        const mfa = supabase.auth.mfa;
        if (mfa) {
          try {
            const { data: assurance } = await mfa.getAuthenticatorAssuranceLevel();
            if (assurance?.nextLevel === "aal2" && assurance.currentLevel !== "aal2") {
              setStatusMessage("Enter your authenticator code to continue.");
              setCompleted(true);
              router.replace(mfaTarget());
              return;
            }
          } catch {
            // Fall through: the workspace endpoint returns mfa_required if a challenge is needed.
          }
        }
        let workspaceResponse: Response;
        try {
          workspaceResponse = await fetch("/api/evidence/personal-workspace", {
            method: "PUT",
            cache: "no-store",
          });
        } catch {
          setErrorMessage("Your account is signed in, but the workspace service could not be reached. Try opening it again.");
          return;
        }
        if (workspaceResponse.status === 403) {
          const payload = (await workspaceResponse.json().catch(() => null)) as { code?: unknown } | null;
          if (payload?.code === "mfa_required") {
            setStatusMessage("Enter your authenticator code to continue.");
            setCompleted(true);
            router.replace(mfaTarget());
            return;
          }
        }
        if (!workspaceResponse.ok) {
          setErrorMessage(workspaceResponse.status === 401
            ? "Sign-in succeeded, but your workspace could not verify this session. Try opening it again."
            : "Your account is signed in, but your workspace could not be opened. Try again later.");
          return;
        }
        setStatusMessage("Signed in. Opening your workspace…");
        setCompleted(true);
        router.replace(safeInternalPath(returnTo));
        return;
      }

      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email: normalizedEmail,
          password,
          options: { emailRedirectTo: authCallbackUrl(window.location.origin, "/") },
        });
        if (error) {
          setErrorMessage("We could not create the account. Check the details or try again later.");
          return;
        }
        if (data.session) {
          const { error: signOutError } = await supabase.auth.signOut();
          setErrorMessage(signOutError
            ? "Email confirmation is not enabled and the session could not be cleared. Sign out before continuing."
            : "Email confirmation is required but is not enabled in the Auth project.");
          return;
        }
        setStatusMessage("Check your email for a confirmation link. You can sign in after confirming your address.");
        return;
      }

      if (mode === "forgot-password") {
        const { error } = await createSupabaseRecoveryRequestClient().auth.resetPasswordForEmail(normalizedEmail, {
          redirectTo: authCallbackUrl(window.location.origin, "/reset-password"),
        });
        if (error) {
          if (error.status === 429) {
            setCompleted(true);
            setErrorMessage("Too many reset requests. Please wait before requesting another email.");
          } else {
            setErrorMessage("We could not request a reset email. Try again later.");
          }
          return;
        }
        setCompleted(true);
        setStatusMessage("If an account matches that address, a reset link will arrive by email.");
        return;
      }

      const response = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 503) {
          const result = await response.json().catch(() => null);
          if (result?.code === "signout-unconfirmed") {
            setPassword("");
            setPasswordConfirmation("");
            setCompleted(true);
            setStatusMessage("Password updated, but automatic sign out could not be confirmed. Sign out before continuing.");
            return;
          }
        }
        setErrorMessage(response.status === 401 || response.status === 403
          ? "The reset link is invalid or expired. Request a new link."
          : "We could not update the password. Try again or request a new link.");
        return;
      }
      setPassword("");
      setPasswordConfirmation("");
      setCompleted(true);
      setStatusMessage("Password updated. Sign in with your new password.");
    } catch {
      setErrorMessage("The authentication service could not be reached. Try again later.");
    } finally {
      submitting.current = false;
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

        {!completed && <form className="auth-form" onSubmit={submit} aria-busy={pending}>
          {((mode === "login" && !sessionEstablished) || mode === "signup" || mode === "forgot-password") && (
            <label>
              <span>Email</span>
              <input
                autoComplete="email"
                autoCapitalize="none"
                type="email"
                maxLength={254}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
          )}
          {((mode === "login" && !sessionEstablished) || mode === "signup" || mode === "reset-password") && (
            <label>
              <span>{mode === "reset-password" ? "New password" : "Password"}</span>
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                type="password"
                minLength={mode === "login" ? undefined : 12}
                maxLength={1024}
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
                minLength={12}
                maxLength={1024}
                value={passwordConfirmation}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
              />
            </label>
          )}
          <button className="auth-submit" type="submit" disabled={pending || !hasConfiguration}>
            {pending ? "Please wait…" : mode === "login" && sessionEstablished ? "Retry opening workspace" : content.button}
          </button>
        </form>}

        {(mode === "signup" || mode === "reset-password") && (
          <p className="auth-password-hint">Use at least 12 characters.</p>
        )}

        <nav className="auth-links" aria-label="Account options">
          {mode === "login" && <Link href="/forgot-password">Forgot password?</Link>}
          {mode === "login" && <Link href="/signup">Create an account</Link>}
          {mode !== "login" && <Link href="/login">Back to sign in</Link>}
        </nav>
      </div>
    </main>
  );
}
