"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { Factor } from "@supabase/supabase-js";
import { createSupabaseBrowserClient } from "../../lib/supabase/client";

type Enrollment = { factorId: string; qrCode: string; secret: string };

type PanelState = "loading" | "challenge" | "enroll" | "verified" | "unavailable";

export default function MfaPanel({ next }: { next: string }) {
  const router = useRouter();
  const [state, setState] = useState<PanelState>("loading");
  const [factors, setFactors] = useState<Factor[]>([]);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const submitting = useRef(false);

  const load = useCallback(async () => {
    const supabase = createSupabaseBrowserClient();
    try {
      const [{ data: factorData, error: factorError }, { data: level, error: levelError }] =
        await Promise.all([
          supabase.auth.mfa.listFactors(),
          supabase.auth.mfa.getAuthenticatorAssuranceLevel(),
        ]);
      if (factorError || levelError) {
        setState("unavailable");
        setMessage("Two-factor authentication could not be loaded. Try again.");
        return;
      }
      const totp = factorData?.totp ?? [];
      setFactors(totp);
      setState(
        level?.currentLevel === "aal2"
          ? "verified"
          : totp.some((factor) => factor.status === "verified")
            ? "challenge"
            : "enroll",
      );
      setMessage("");
    } catch {
      setState("unavailable");
      setMessage("Two-factor authentication could not be loaded. Try again.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function startEnrollment() {
    if (submitting.current) return;
    submitting.current = true;
    setPending(true);
    setMessage("");
    const supabase = createSupabaseBrowserClient();
    try {
      for (const factor of factors.filter((item) => item.status === "unverified")) {
        await supabase.auth.mfa.unenroll({ factorId: factor.id });
      }
      const { data, error } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        friendlyName: "Evidence Layer authenticator",
      });
      if (error || !data) {
        setMessage("The authenticator could not be set up. Try again.");
        return;
      }
      setEnrollment({
        factorId: data.id,
        qrCode: data.totp.qr_code,
        secret: data.totp.secret,
      });
      setCode("");
    } catch {
      setMessage("The authenticator could not be set up. Try again.");
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const factorId = enrollment?.factorId ?? factors.find((factor) => factor.status === "verified")?.id;
    if (!factorId) {
      setMessage("No authenticator is available to verify.");
      return;
    }
    const normalized = code.replace(/\s+/g, "");
    if (!/^\d{6}$/.test(normalized)) {
      setMessage("Enter the 6-digit code from your authenticator app.");
      return;
    }
    submitting.current = true;
    setPending(true);
    setMessage("");
    const supabase = createSupabaseBrowserClient();
    try {
      const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({ factorId });
      if (challengeError || !challenge) {
        setMessage("The verification challenge could not be created. Try again.");
        return;
      }
      const { error: verifyError } = await supabase.auth.mfa.verify({
        factorId,
        challengeId: challenge.id,
        code: normalized,
      });
      if (verifyError) {
        setMessage("That code was not accepted. Check the time on your device and try again.");
        return;
      }
      router.replace(next);
      router.refresh();
    } catch {
      setMessage("The code could not be verified. Try again.");
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  async function removeFactor(factorId: string) {
    if (submitting.current) return;
    submitting.current = true;
    setPending(true);
    setMessage("");
    try {
      const { error } = await createSupabaseBrowserClient().auth.mfa.unenroll({ factorId });
      if (error) {
        setMessage("The authenticator could not be removed. Try again.");
        return;
      }
      setEnrollment(null);
      setCode("");
      await load();
    } catch {
      setMessage("The authenticator could not be removed. Try again.");
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  if (state === "loading") {
    return (
      <section className="workspace-panel mfa-panel" aria-live="polite">
        <p className="eyebrow">Account security</p>
        <h1>Two-factor authentication</h1>
        <p>Checking this account&apos;s authenticators…</p>
      </section>
    );
  }

  return (
    <section className="workspace-panel mfa-panel" aria-labelledby="mfa-title">
      <p className="eyebrow">Account security</p>
      <h1 id="mfa-title">Two-factor authentication</h1>

      {state === "verified" && (
        <>
          <p className="mfa-status mfa-status--verified">Verified for this session</p>
          <p>
            This session completed the authenticator challenge. Removing an authenticator does
            not revoke existing access tokens; sign out everywhere after a security change.
          </p>
          <ul className="mfa-factors">
            {factors.filter((factor) => factor.status === "verified").map((factor) => (
              <li key={factor.id}>
                <span>{factor.friendly_name || "Authenticator app"}</span>
                <button
                  type="button"
                  onClick={() => removeFactor(factor.id)}
                  disabled={pending}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <p className="workspace-form-actions">
            <button type="button" onClick={() => router.replace(next)} disabled={pending}>
              Continue
            </button>
          </p>
        </>
      )}

      {state === "challenge" && (
        <>
          <p className="mfa-status">A verified authenticator is enrolled for this account.</p>
          <p>Enter the current 6-digit code to finish signing in.</p>
          <form className="workspace-form mfa-form" onSubmit={verify}>
            <label htmlFor="mfa-code">Authenticator code</label>
            <input
              id="mfa-code"
              className="mfa-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              required
              disabled={pending}
            />
            <button type="submit" disabled={pending}>
              {pending ? "Verifying…" : "Verify"}
            </button>
          </form>
        </>
      )}

      {state === "enroll" && (
        <>
          <p>
            Add a time-based one-time password (TOTP) authenticator to require a second step
            after your password. Supported apps include 1Password, Authy, Google Authenticator,
            and Microsoft Authenticator.
          </p>
          {!enrollment ? (
            <p className="workspace-form-actions">
              <button type="button" onClick={startEnrollment} disabled={pending}>
                {pending ? "Preparing…" : "Set up authenticator"}
              </button>
            </p>
          ) : (
            <>
              <p>Scan this code with your authenticator app, then enter the 6-digit code.</p>
              {/* The QR code is generated by Supabase Auth as an SVG data URI. */}
              <img
                className="mfa-qr"
                src={enrollment.qrCode}
                alt="Authenticator enrollment QR code"
                width={180}
                height={180}
              />
              <p>
                Cannot scan? Enter this secret manually:{" "}
                <code className="mfa-secret">{enrollment.secret}</code>
              </p>
              <form className="workspace-form mfa-form" onSubmit={verify}>
                <label htmlFor="mfa-code">Authenticator code</label>
                <input
                  id="mfa-code"
                  className="mfa-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  required
                  disabled={pending}
                />
                <button type="submit" disabled={pending}>
                  {pending ? "Verifying…" : "Verify and enable"}
                </button>
              </form>
            </>
          )}
        </>
      )}

      {state === "unavailable" && (
        <p role="alert">Two-factor authentication is unavailable right now. Try again later.</p>
      )}

      {message && <p className="workspace-message" role="alert">{message}</p>}
    </section>
  );
}
