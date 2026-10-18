"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "../lib/supabase/client";

type Workspace = { id: string; name: string; role: "owner" | "member" };
type PanelState = "loading" | "ready" | "expired" | "unavailable";

export default function AuthAccount() {
  const router = useRouter();
  const [state, setState] = useState<PanelState>("loading");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);

  const loadWorkspaces = useCallback(async () => {
    setState("loading");
    try {
      const response = await fetch("/api/evidence/me", { cache: "no-store" });
      if (response.status === 401) {
        setState("expired");
        return;
      }
      if (!response.ok) {
        setState("unavailable");
        return;
      }
      const payload = (await response.json()) as { workspaces?: Workspace[] };
      setWorkspaces(Array.isArray(payload.workspaces) ? payload.workspaces : []);
      setState("ready");
    } catch {
      setState("unavailable");
    }
  }, []);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  async function createPersonalWorkspace() {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/evidence/personal-workspace", {
        method: "PUT",
        cache: "no-store",
      });
      if (response.status === 401) {
        setState("expired");
      } else if (response.status === 403) {
        setMessage("A personal workspace is unavailable for this account.");
      } else if (!response.ok) {
        setMessage("The workspace could not be created. Try again later.");
      } else {
        await loadWorkspaces();
        setMessage("Your personal workspace is ready.");
      }
    } catch {
      setMessage("The workspace service could not be reached. Try again later.");
    } finally {
      setPending(false);
    }
  }

  async function openWorkspace(workspace: Workspace) {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch(`/api/evidence/workspaces/${encodeURIComponent(workspace.id)}`, {
        cache: "no-store",
      });
      if (response.status === 401) {
        setState("expired");
      } else if (response.status === 404) {
        setMessage("Workspace not found or access is unavailable.");
      } else if (!response.ok) {
        setMessage("Workspace access could not be verified. Try again later.");
      } else {
        const verified = (await response.json()) as Workspace;
        setMessage(`Access verified: ${verified.name} (${verified.role}).`);
      }
    } catch {
      setMessage("Workspace access could not be verified. Try again later.");
    } finally {
      setPending(false);
    }
  }

  async function signOut() {
    setPending(true);
    setMessage("");
    try {
      const { error } = await createSupabaseBrowserClient().auth.signOut();
      if (error) {
        setMessage("Sign out could not be confirmed. Try again.");
        return;
      }
      router.replace("/login?reason=signed-out");
      router.refresh();
    } catch {
      setMessage("Sign out could not be confirmed. Try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="account-panel" aria-labelledby="account-heading">
      <div className="account-panel-heading">
        <span className="sidebar-label" id="account-heading">ACCOUNT WORKSPACE</span>
        <button className="account-signout" type="button" onClick={signOut} disabled={pending}>
          Sign out
        </button>
      </div>
      {state === "loading" && <p role="status">Checking workspace access…</p>}
      {state === "expired" && (
        <p role="alert">Your session expired. <a href="/login?reason=session-expired">Sign in again</a>.</p>
      )}
      {state === "unavailable" && <p role="status">Workspace service is unavailable.</p>}
      {state === "ready" && workspaces.length === 0 && (
        <>
          <p>No personal workspace is set up for this account yet.</p>
          <button className="account-action" type="button" onClick={createPersonalWorkspace} disabled={pending}>
            Create personal workspace
          </button>
        </>
      )}
      {state === "ready" && workspaces.length > 0 && (
        <ul className="account-workspaces" aria-label="Your authorized workspaces">
          {workspaces.map((workspace) => (
            <li key={workspace.id}>
              <span><strong>{workspace.name}</strong><small>{workspace.role}</small></span>
              <button
                className="account-action"
                type="button"
                disabled={pending}
                onClick={() => openWorkspace(workspace)}
              >
                Verify access
              </button>
            </li>
          ))}
        </ul>
      )}
      {message && <p className="account-message" role="status">{message}</p>}
      <p className="account-sample-note">The Explorer below remains a synthetic demo; no evidence is stored in this workspace yet.</p>
    </section>
  );
}
