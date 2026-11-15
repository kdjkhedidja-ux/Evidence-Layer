"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

export default function AcceptInvitationPage() {
  const params = useParams<{ token: string }>();
  const [message, setMessage] = useState("Accepting the invitation…");

  useEffect(() => {
    const token = params?.token;
    if (!token) {
      setMessage("This invitation link is invalid.");
      return;
    }
    let cancelled = false;
    async function accept() {
      try {
        const response = await fetch("/api/evidence/invitations/accept", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
          cache: "no-store",
        });
        if (cancelled) return;
        if (response.status === 401) {
          window.location.assign(`/login?next=${encodeURIComponent(`/invite/${token}`)}`);
          return;
        }
        if (!response.ok) {
          setMessage("This invitation is no longer available.");
          return;
        }
        setMessage("Invitation accepted. Opening the workspace…");
        window.location.assign("/workspace");
      } catch {
        if (!cancelled) setMessage("Invitations are unavailable right now.");
      }
    }
    void accept();
    return () => {
      cancelled = true;
    };
  }, [params?.token]);

  return (
    <main className="workspace-page">
      <section className="workspace-panel" aria-live="polite">
        <p className="eyebrow">Workspace invitation</p>
        <h1>Join workspace</h1>
        <p>{message}</p>
      </section>
    </main>
  );
}
