"use client";

import { useEffect, useState } from "react";

type ErrorReason = "expired-link" | "invalid-link" | "configuration";

function normalizeReason(reason: string | undefined): ErrorReason {
  if (reason === "expired-link" || reason === "configuration") return reason;
  return "invalid-link";
}

function reasonFromFragment(hash: string): ErrorReason | null {
  if (!hash.startsWith("#") || hash.length === 1) return null;

  const params = new URLSearchParams(hash.slice(1));
  const errors = params.getAll("error");
  const errorCodes = params.getAll("error_code");
  const hasErrorDetails =
    errors.length > 0 || errorCodes.length > 0 || params.has("error_description");

  if (!hasErrorDetails) return null;
  if (errors.length > 1 || errorCodes.length > 1) return "invalid-link";

  const errorCode = errorCodes[0];
  if (errorCode === "otp_expired" || errorCode === "access_token_expired") {
    return "expired-link";
  }
  return "invalid-link";
}

function contentFor(reason: ErrorReason): { title: string; description: string } {
  switch (reason) {
    case "expired-link":
      return {
        title: "This link has expired",
        description: "Request a new confirmation or password-reset email and use the latest link.",
      };
    case "configuration":
      return {
        title: "Authentication is unavailable",
        description: "The application does not have its required Supabase public configuration.",
      };
    default:
      return {
        title: "This link could not be verified",
        description: "The link may be invalid or already used. Request a new email to continue.",
      };
  }
}

export default function AuthErrorMessage({ initialReason }: { initialReason?: string }) {
  const [reason, setReason] = useState(() => normalizeReason(initialReason));

  useEffect(() => {
    const { hash, pathname, search } = window.location;
    if (!hash) return;

    const fragmentReason = reasonFromFragment(hash);
    if (fragmentReason) setReason(fragmentReason);

    // Fragments are not sent to the callback route. Remove them from the
    // address bar without exchanging codes or creating a Supabase session.
    window.history.replaceState(window.history.state, "", `${pathname}${search}`);
  }, []);

  const content = contentFor(reason);
  return (
    <>
      <h1 id="auth-error-title">{content.title}</h1>
      <p className="auth-description">{content.description}</p>
    </>
  );
}
