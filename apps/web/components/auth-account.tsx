"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "../lib/supabase/client";
import type { DeletionRequestView } from "../lib/deletion-api";

type Workspace = { id: string; name: string; role: "owner" | "member" };
type PanelState = "loading" | "ready" | "expired" | "unavailable";

const DELETION_STATUS_LABELS: Record<DeletionRequestView["status"], string> = {
  approval_pending: "Approval pending",
  journal_ack_pending: "Accepted locally; purge pending",
  accepted: "Accepted",
};

export default function AuthAccount({ userId, email }: { userId: string; email: string }) {
  const router = useRouter();
  const [state, setState] = useState<PanelState>("loading");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [deletion, setDeletion] = useState<DeletionRequestView | null>(null);
  const [deletionPending, setDeletionPending] = useState(false);
  const [deletionMessage, setDeletionMessage] = useState("");
  const [deletionConfirmed, setDeletionConfirmed] = useState(false);
  const [lookupId, setLookupId] = useState("");
  const deletionRequestsEnabled = process.env.NEXT_PUBLIC_DELETION_REQUESTS_ENABLED === "true";

  const loadWorkspaces = useCallback(async (): Promise<Workspace[] | null> => {
    setState("loading");
    try {
      const response = await fetch("/api/evidence/me", { cache: "no-store" });
      if (response.status === 401) {
        setState("expired");
        return null;
      }
      if (!response.ok) {
        setState("unavailable");
        return null;
      }
      const payload = (await response.json()) as { user_id?: string; workspaces?: Workspace[] };
      if (payload.user_id !== userId) {
        setWorkspaces([]);
        setState("expired");
        router.refresh();
        return null;
      }
      const authorizedWorkspaces = Array.isArray(payload.workspaces) ? payload.workspaces : [];
      setWorkspaces(authorizedWorkspaces);
      setState("ready");
      return authorizedWorkspaces;
    } catch {
      setState("unavailable");
      return null;
    }
  }, [router, userId]);

  const createPersonalWorkspace = useCallback(async () => {
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
        setState("unavailable");
        setMessage("A personal workspace is unavailable for this account.");
      } else if (!response.ok) {
        setState("unavailable");
        setMessage("The workspace could not be created. Try again later.");
      } else {
        const provisioned = (await response.json()) as Workspace;
        const memberships = await loadWorkspaces();
        if (memberships?.some((workspace) => workspace.id === provisioned.id)) {
          setMessage("Your personal workspace is ready.");
        } else if (memberships) {
          setState("unavailable");
          setMessage("Workspace access could not be confirmed. Try again later.");
        }
      }
    } catch {
      setState("unavailable");
      setMessage("The workspace service could not be reached. Try again later.");
    } finally {
      setPending(false);
    }
  }, [loadWorkspaces]);

  useEffect(() => {
    void createPersonalWorkspace();
  }, [createPersonalWorkspace]);

  useEffect(() => {
    const { data: { subscription } } = createSupabaseBrowserClient().auth.onAuthStateChange(
      (event, session) => {
        if (event === "SIGNED_OUT" || (event === "SIGNED_IN" && session?.user.id !== userId)) {
          setWorkspaces([]);
          setState("expired");
          router.refresh();
        }
      },
    );
    return () => subscription.unsubscribe();
  }, [router, userId]);

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
      setWorkspaces([]);
      setState("expired");
      router.replace("/login?reason=signed-out");
    } catch {
      setMessage("Sign out could not be confirmed. Try again.");
    } finally {
      setPending(false);
    }
  }

  const readDeletionResponse = useCallback(async (response: Response): Promise<DeletionRequestView | null> => {
    let payload: unknown = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (!response.ok) {
      const detail = payload && typeof payload === "object" && typeof (payload as { error?: unknown }).error === "string"
        ? (payload as { error: string }).error
        : "Deletion request is unavailable.";
      setDeletionMessage(detail);
      return null;
    }
    setDeletionMessage("");
    return payload as DeletionRequestView;
  }, []);

  const loadDeletion = useCallback(async (requestId: string) => {
    setDeletionPending(true);
    try {
      const response = await fetch(`/api/evidence/deletion-requests/${encodeURIComponent(requestId)}`, { cache: "no-store" });
      if (response.status === 401) { setState("expired"); return; }
      const view = await readDeletionResponse(response);
      if (view) setDeletion(view);
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [readDeletionResponse]);

  const requestDeletion = useCallback(async () => {
    setDeletionPending(true);
    setDeletionMessage("");
    try {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const response = await fetch("/api/evidence/deletion-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
      });
      if (response.status === 401) { setState("expired"); return; }
      const view = await readDeletionResponse(response);
      if (view) { setDeletion(view); setDeletionConfirmed(false); }
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [readDeletionResponse]);

  const acceptDeletion = useCallback(async () => {
    if (!deletion) return;
    setDeletionPending(true);
    setDeletionMessage("");
    try {
      const response = await fetch(`/api/evidence/deletion-requests/${deletion.request_id}/accept`, {
        method: "POST",
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      const view = await readDeletionResponse(response);
      if (view) {
        setDeletion(view);
        setDeletionConfirmed(false);
        setMessage("Deletion accepted. Data access for this account is now blocked.");
      }
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [deletion, readDeletionResponse]);

  return (
    <section className="account-panel" id="account-panel" aria-labelledby="account-heading">
      <div className="account-panel-heading">
        <span className="sidebar-label" id="account-heading">ACCOUNT WORKSPACE</span>
        <button className="account-signout" type="button" onClick={signOut} disabled={pending}>
          Sign out
        </button>
      </div>
      <p className="account-email">{email}</p>
      {state === "loading" && <p role="status">Checking workspace access…</p>}
      {state === "expired" && (
        <p role="alert">Your session expired. <a href="/login?reason=session-expired">Sign in again</a>.</p>
      )}
      {state === "unavailable" && (
        <>
          <p role="status">Workspace service is unavailable.</p>
          <button className="account-action" type="button" onClick={createPersonalWorkspace} disabled={pending}>
            Retry workspace setup
          </button>
        </>
      )}
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
      <p className="account-sample-note">The Explorer shows only records your verified account is authorized to read; no sample records are substituted.</p>
      {deletionRequestsEnabled ? (
      <section className="account-deletion" aria-labelledby="account-deletion-title">
        <h3 id="account-deletion-title">Delete account</h3>
        <p className="account-deletion-scope">
          This requests deletion of the account data this service manages. Evidence and
          relationships in shared workspaces, workspace ownership, snapshots, and stored files are
          retained by the current deletion slice. Accepting a request blocks access immediately and
          cannot be cancelled.
        </p>
        {deletion ? (
          <div className="account-deletion-state">
            <p>Request <code>{deletion.request_id}</code></p>
            <p>
              Status: <strong>{DELETION_STATUS_LABELS[deletion.status]}</strong>
            </p>
            {deletion.status === "approval_pending" ? (
              <>
                <label className="account-deletion-confirm">
                  <input
                    type="checkbox"
                    checked={deletionConfirmed}
                    onChange={(event) => setDeletionConfirmed(event.target.checked)}
                  />
                  I understand that accepting blocks access to this account&apos;s data.
                </label>
                <div className="account-deletion-actions">
                  <button
                    type="button"
                    disabled={!deletionConfirmed || deletionPending}
                    onClick={() => void acceptDeletion()}
                  >
                    {deletionPending ? "Accepting…" : "Accept deletion request"}
                  </button>
                  <button type="button" disabled={deletionPending} onClick={() => void loadDeletion(deletion.request_id)}>
                    Refresh status
                  </button>
                </div>
              </>
            ) : (
              <div className="account-deletion-actions">
                <button type="button" disabled={deletionPending} onClick={() => void loadDeletion(deletion.request_id)}>
                  Refresh status
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="account-deletion-actions">
            <button type="button" disabled={deletionPending} onClick={() => void requestDeletion()}>
              {deletionPending ? "Requesting…" : "Request account deletion"}
            </button>
          </div>
        )}
        <div className="account-deletion-lookup">
          <label htmlFor="deletion-request-id">Have a request ID?</label>
          <input
            id="deletion-request-id"
            value={lookupId}
            placeholder="00000000-0000-4000-8000-000000000000"
            onChange={(event) => setLookupId(event.target.value)}
          />
          <button
            type="button"
            disabled={!lookupId.trim() || deletionPending}
            onClick={() => void loadDeletion(lookupId.trim())}
          >
            Load status
          </button>
        </div>
        {deletionMessage ? <p className="account-message" role="alert">{deletionMessage}</p> : null}
      </section>
      ) : null}
    </section>
  );
}
