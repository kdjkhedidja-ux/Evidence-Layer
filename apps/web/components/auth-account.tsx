"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { createSupabaseBrowserClient } from "../lib/supabase/client";
import { deletionRequestsEnabled as resolveDeletionRequestsEnabled } from "../lib/deletion-flag";
import { accountExportEnabled as resolveAccountExportEnabled } from "../lib/export-flag";
import { googleDriveConnectorEnabled as resolveGoogleDriveConnectorEnabled } from "../lib/connector-flags";
import type { DeletionRequestView } from "../lib/deletion-api";

type Workspace = { id: string; name: string; role: "owner" | "admin" | "member" };
type OrganizationMember = { user_id: string; email: string; role: "admin" | "member" };
type OrganizationWorkspace = { id: string; name: string };
type OrganizationOverview = {
  id: string;
  name: string;
  owner_user_id: string;
  members: OrganizationMember[];
  workspaces: OrganizationWorkspace[];
};
type PanelState = "loading" | "ready" | "expired" | "unavailable";
type ConnectorConnection = {
  connection_id: string;
  provider: string;
  account_email: string | null;
  status: string;
  last_sync_at: string | null;
  last_error_category: string | null;
};

const DELETION_STATUS_LABELS: Record<DeletionRequestView["status"], string> = {
  approval_pending: "Approval pending",
  journal_ack_pending: "Accepted locally; purge pending",
  accepted: "Accepted; purge queued",
  purge_pending: "Purge in progress",
  purge_failed: "Purge needs attention",
  purged: "Purged",
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
  const [personalDeletionPending, setPersonalDeletionPending] = useState(false);
  const [personalDeletionMessage, setPersonalDeletionMessage] = useState("");
  const [exportJob, setExportJob] = useState<{ export_id: string; expires_at: string | null; grant_token: string | null } | null>(null);
  const [exportPending, setExportPending] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [connectors, setConnectors] = useState<ConnectorConnection[]>([]);
  const [connectorPending, setConnectorPending] = useState(false);
  const [connectorMessage, setConnectorMessage] = useState("");
  const [lookupId, setLookupId] = useState("");
  const [accountType, setAccountType] = useState<"individual" | "organization">("individual");
  const [organizationName, setOrganizationName] = useState<string | null>(null);
  const [organizationInput, setOrganizationInput] = useState("");
  const [organizationPending, setOrganizationPending] = useState(false);
  const [organizationMessage, setOrganizationMessage] = useState("");
  const [organization, setOrganization] = useState<OrganizationOverview | null>(null);
  const [organizationLoading, setOrganizationLoading] = useState(false);
  const [organizationError, setOrganizationError] = useState("");
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"admin" | "member">("member");
  const [memberPending, setMemberPending] = useState(false);
  const [memberActionPending, setMemberActionPending] = useState<string | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspacePending, setWorkspacePending] = useState(false);
  const [workspaceActionPending, setWorkspaceActionPending] = useState<string | null>(null);
  const deletionRequestsEnabled = resolveDeletionRequestsEnabled({
    flag: process.env.NEXT_PUBLIC_DELETION_REQUESTS_ENABLED,
  });
  const accountExportEnabled = resolveAccountExportEnabled({
    flag: process.env.NEXT_PUBLIC_ACCOUNT_EXPORT_ENABLED,
  });
  const googleDriveConnectorEnabled = resolveGoogleDriveConnectorEnabled({
    flag: process.env.NEXT_PUBLIC_GOOGLE_DRIVE_CONNECTOR_ENABLED,
  });

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
      const payload = (await response.json()) as {
        user_id?: string;
        workspaces?: Workspace[];
        account_type?: string;
        organization_name?: string | null;
      };
      if (payload.user_id !== userId) {
        setWorkspaces([]);
        setState("expired");
        router.refresh();
        return null;
      }
      const authorizedWorkspaces = Array.isArray(payload.workspaces) ? payload.workspaces : [];
      setWorkspaces(authorizedWorkspaces);
      setAccountType(payload.account_type === "organization" ? "organization" : "individual");
      setOrganizationName(
        payload.account_type === "organization" && typeof payload.organization_name === "string"
          ? payload.organization_name
          : null,
      );
      setState("ready");
      return authorizedWorkspaces;
    } catch {
      setState("unavailable");
      return null;
    }
  }, [router, userId]);

  const createOrganization = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = organizationInput.trim();
    if (!name) {
      setOrganizationMessage("Enter an organization name.");
      return;
    }
    setOrganizationPending(true);
    setOrganizationMessage("");
    try {
      const response = await fetch("/api/evidence/me/organization", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ name }),
      });
      if (response.status === 401) {
        setState("expired");
        return;
      }
      if (!response.ok) {
        setOrganizationMessage("Organization account could not be created.");
        return;
      }
      const payload = (await response.json()) as {
        account_type?: string;
        organization_name?: string | null;
      };
      setAccountType(payload.account_type === "organization" ? "organization" : "individual");
      setOrganizationName(
        typeof payload.organization_name === "string" ? payload.organization_name : null,
      );
      setOrganizationInput("");
      setOrganizationMessage("Organization account is ready. Workspace & Access is enabled.");
      await loadWorkspaces();
    } catch {
      setOrganizationMessage("Organization account could not be created.");
    } finally {
      setOrganizationPending(false);
    }
  }, [loadWorkspaces, organizationInput]);

  const loadOrganization = useCallback(async () => {
    setOrganizationLoading(true);
    setOrganizationError("");
    try {
      const response = await fetch("/api/evidence/organization", { cache: "no-store" });
      if (response.status === 401) { setState("expired"); return; }
      if (response.status === 403) {
        setOrganization(null);
        setOrganizationError("Organization admin access is required.");
        return;
      }
      if (!response.ok) {
        setOrganizationError("Organization service is unavailable.");
        return;
      }
      setOrganization((await response.json()) as OrganizationOverview);
    } catch {
      setOrganizationError("Organization service is unavailable.");
    } finally {
      setOrganizationLoading(false);
    }
  }, []);

  useEffect(() => {
    if (accountType === "organization") {
      void loadOrganization();
    } else {
      setOrganization(null);
    }
  }, [accountType, loadOrganization]);

  const addOrganizationMember = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const email = memberEmail.trim();
    if (!email) return;
    setMemberPending(true);
    setOrganizationError("");
    try {
      const response = await fetch("/api/evidence/organization/members", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ email, role: memberRole }),
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) { setOrganizationError("Member could not be added."); return; }
      setMemberEmail("");
      await loadOrganization();
    } catch {
      setOrganizationError("Member could not be added.");
    } finally {
      setMemberPending(false);
    }
  }, [loadOrganization, memberEmail, memberRole]);

  const updateOrganizationMemberRole = useCallback(async (userId: string, role: "admin" | "member") => {
    setMemberActionPending(userId);
    setOrganizationError("");
    try {
      const response = await fetch(`/api/evidence/organization/members/${encodeURIComponent(userId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ role }),
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) { setOrganizationError("Member role could not be updated."); return; }
      await loadOrganization();
    } catch {
      setOrganizationError("Member role could not be updated.");
    } finally {
      setMemberActionPending(null);
    }
  }, [loadOrganization]);

  const removeOrganizationMember = useCallback(async (userId: string) => {
    if (!window.confirm("Remove this member from the organization?")) return;
    setMemberActionPending(userId);
    setOrganizationError("");
    try {
      const response = await fetch(`/api/evidence/organization/members/${encodeURIComponent(userId)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) { setOrganizationError("Member could not be removed."); return; }
      await loadOrganization();
    } catch {
      setOrganizationError("Member could not be removed.");
    } finally {
      setMemberActionPending(null);
    }
  }, [loadOrganization]);

  const deleteGroupWorkspace = useCallback(async (workspaceId: string, name: string) => {
    if (!window.confirm(`Delete the group workspace "${name}" and all of its data?`)) return;
    setWorkspaceActionPending(workspaceId);
    setOrganizationError("");
    try {
      const response = await fetch(`/api/evidence/organization/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) { setOrganizationError("Group workspace could not be deleted."); return; }
      await loadOrganization();
    } catch {
      setOrganizationError("Group workspace could not be deleted.");
    } finally {
      setWorkspaceActionPending(null);
    }
  }, [loadOrganization]);

  const createGroupWorkspace = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = workspaceName.trim();
    if (!name) return;
    setWorkspacePending(true);
    setOrganizationError("");
    try {
      const response = await fetch("/api/evidence/organization/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ name }),
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) { setOrganizationError("Group workspace could not be created."); return; }
      setWorkspaceName("");
      await loadOrganization();
    } catch {
      setOrganizationError("Group workspace could not be created.");
    } finally {
      setWorkspacePending(false);
    }
  }, [loadOrganization, workspaceName]);

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

  const createAccountExport = useCallback(async (): Promise<void> => {
    setExportPending(true);
    setExportMessage("");
    try {
      const response = await fetch("/api/evidence/me/exports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      if (response.status === 401) {
        setExportMessage("Your session expired. Sign in again.");
        return;
      }
      if (!response.ok) {
        setExportMessage("The export could not be created.");
        return;
      }
      const payload = (await response.json()) as {
        export_id?: string;
        expires_at?: string | null;
        grant_token?: string | null;
      };
      if (!payload.export_id) {
        setExportMessage("The export could not be created.");
        return;
      }
      setExportJob({
        export_id: payload.export_id,
        expires_at: payload.expires_at ?? null,
        grant_token: payload.grant_token ?? null,
      });
      setExportMessage(
        payload.grant_token
          ? "Export ready. Download it once before it expires."
          : "An export already exists for this request; request a new export for a new download grant.",
      );
    } catch {
      setExportMessage("The export service is unavailable.");
    } finally {
      setExportPending(false);
    }
  }, []);

  const downloadAccountExport = useCallback(async (): Promise<void> => {
    if (!exportJob?.grant_token) return;
    setExportPending(true);
    setExportMessage("");
    try {
      const response = await fetch(
        `/api/evidence/me/exports/${encodeURIComponent(exportJob.export_id)}/download`,
        {
          headers: { "X-Export-Grant": exportJob.grant_token },
          cache: "no-store",
        },
      );
      if (!response.ok) {
        setExportMessage("The export download is unavailable or expired.");
        setExportJob({ ...exportJob, grant_token: null });
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `evidence-layer-export-${exportJob.export_id}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setExportJob({ ...exportJob, grant_token: null });
      setExportMessage("Download started. The grant is now consumed.");
    } catch {
      setExportMessage("The export download is unavailable.");
    } finally {
      setExportPending(false);
    }
  }, [exportJob]);

  const revokeAccountExport = useCallback(async (): Promise<void> => {
    if (!exportJob) return;
    setExportPending(true);
    setExportMessage("");
    try {
      const response = await fetch(
        `/api/evidence/me/exports/${encodeURIComponent(exportJob.export_id)}/revoke`,
        { method: "POST" },
      );
      if (!response.ok) {
        setExportMessage("The export could not be revoked.");
        return;
      }
      setExportJob(null);
      setExportMessage("Export revoked and artifact removed.");
    } catch {
      setExportMessage("The export service is unavailable.");
    } finally {
      setExportPending(false);
    }
  }, [exportJob]);

  const loadConnectors = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch("/api/evidence/connectors", { cache: "no-store" });
      if (!response.ok) return;
      const payload = (await response.json()) as ConnectorConnection[];
      setConnectors(Array.isArray(payload) ? payload : []);
    } catch {
      // The connector panel is optional; leave the existing state intact.
    }
  }, []);

  const syncConnector = useCallback(async (connectionId: string): Promise<void> => {
    setConnectorPending(true);
    setConnectorMessage("");
    try {
      const response = await fetch(
        `/api/evidence/connectors/${encodeURIComponent(connectionId)}/sync`,
        { method: "POST" },
      );
      if (!response.ok) {
        setConnectorMessage("The connector sync could not be completed.");
        return;
      }
      const payload = (await response.json()) as { ingested?: number; skipped?: number };
      setConnectorMessage(`Sync complete: ${payload.ingested ?? 0} documents ingested, ${payload.skipped ?? 0} skipped.`);
      await loadConnectors();
    } catch {
      setConnectorMessage("The connector service is unavailable.");
    } finally {
      setConnectorPending(false);
    }
  }, [loadConnectors]);

  const disconnectConnector = useCallback(async (connectionId: string): Promise<void> => {
    setConnectorPending(true);
    setConnectorMessage("");
    try {
      const response = await fetch(
        `/api/evidence/connectors/${encodeURIComponent(connectionId)}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        setConnectorMessage("The connector could not be disconnected.");
        return;
      }
      setConnectorMessage("Connector disconnected and stored connector documents removed.");
      await loadConnectors();
    } catch {
      setConnectorMessage("The connector service is unavailable.");
    } finally {
      setConnectorPending(false);
    }
  }, [loadConnectors]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connector")) void loadConnectors();
  }, [loadConnectors]);

  const deletionStorageKey = `evidence-layer:deletion-request:${userId}`;

  useEffect(() => {
    const saved = globalThis.localStorage?.getItem(deletionStorageKey);
    if (saved) void loadDeletion(saved);
  }, [deletionStorageKey, loadDeletion]);

  const loadCurrentDeletion = useCallback(async () => {
    setDeletionPending(true);
    setDeletionMessage("");
    try {
      const response = await fetch("/api/evidence/me/deletion-request", {
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      if (response.status === 404) {
        setDeletionMessage("No deletion request was found for this account.");
        return;
      }
      const view = await readDeletionResponse(response);
      if (view) {
        setDeletion(view);
        globalThis.localStorage?.setItem(deletionStorageKey, view.request_id);
      }
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [deletionStorageKey, readDeletionResponse]);

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
      if (view) {
        setDeletion(view);
        setDeletionConfirmed(false);
        globalThis.localStorage?.setItem(deletionStorageKey, view.request_id);
      }
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [deletionStorageKey, readDeletionResponse]);

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
        globalThis.localStorage?.setItem(deletionStorageKey, view.request_id);
      }
    } catch {
      setDeletionMessage("Deletion request is unavailable.");
    } finally {
      setDeletionPending(false);
    }
  }, [deletion, deletionStorageKey, readDeletionResponse]);

  const deletePersonalData = useCallback(async () => {
    if (!window.confirm("Delete all data in your personal workspace? Your account and workspace remain.")) {
      return;
    }
    setPersonalDeletionPending(true);
    setPersonalDeletionMessage("");
    try {
      const response = await fetch("/api/evidence/me/data-deletions", {
        method: "POST",
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) {
        setPersonalDeletionMessage("Personal workspace data could not be deleted.");
        return;
      }
      const payload = (await response.json()) as {
        evidence_deleted?: number;
        storage_cleanup_required?: boolean;
      };
      setPersonalDeletionMessage(
        `Deleted ${payload.evidence_deleted ?? 0} evidence records.` +
          (payload.storage_cleanup_required ? " Storage cleanup is queued." : ""),
      );
      await loadWorkspaces();
    } catch {
      setPersonalDeletionMessage("Personal workspace data could not be deleted.");
    } finally {
      setPersonalDeletionPending(false);
    }
  }, [loadWorkspaces]);

  const deletePersonalWorkspace = useCallback(async () => {
    if (!window.confirm("Delete your personal workspace and all of its data? Your account remains.")) {
      return;
    }
    setPersonalDeletionPending(true);
    setPersonalDeletionMessage("");
    try {
      const response = await fetch("/api/evidence/me/personal-workspace", {
        method: "DELETE",
        cache: "no-store",
      });
      if (response.status === 401) { setState("expired"); return; }
      if (!response.ok) {
        setPersonalDeletionMessage("Personal workspace could not be deleted.");
        return;
      }
      const payload = (await response.json()) as { storage_cleanup_required?: boolean };
      setPersonalDeletionMessage(
        "Personal workspace deleted." +
          (payload.storage_cleanup_required ? " Storage cleanup is queued." : ""),
      );
      await loadWorkspaces();
    } catch {
      setPersonalDeletionMessage("Personal workspace could not be deleted.");
    } finally {
      setPersonalDeletionPending(false);
    }
  }, [loadWorkspaces]);

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
      {state !== "expired" && (
        <section className="account-organization" aria-labelledby="account-organization-title">
          <h3 id="account-organization-title">Organization</h3>
          {accountType === "organization" ? (
            <>
              <p>Organization: <strong>{organization?.name ?? organizationName}</strong></p>
              <section className="account-access" aria-labelledby="account-access-title">
                <h3 id="account-access-title">Workspace & Access</h3>
                {organizationLoading && <p role="status">Loading organization…</p>}
                {organizationError && <p className="account-message" role="alert">{organizationError}</p>}
                {organization && (
                  <>
                    <h4>Members</h4>
                    <ul className="organization-members">
                      {organization.members.map((member) => (
                        <li key={member.user_id}>
                          <span>{member.email}</span>
                          {member.user_id === organization.owner_user_id ? (
                            <strong>Owner</strong>
                          ) : (
                            <>
                              <select
                                aria-label={`Role for ${member.email}`}
                                value={member.role}
                                disabled={memberActionPending === member.user_id}
                                onChange={(event) => void updateOrganizationMemberRole(
                                  member.user_id,
                                  event.target.value as "admin" | "member",
                                )}
                              >
                                <option value="admin">admin</option>
                                <option value="member">member</option>
                              </select>
                              <button
                                type="button"
                                disabled={memberActionPending === member.user_id}
                                onClick={() => void removeOrganizationMember(member.user_id)}
                              >
                                Remove
                              </button>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                    <form className="account-organization-form" onSubmit={(event) => void addOrganizationMember(event)}>
                      <label htmlFor="organization-member-email">Member email</label>
                      <input
                        id="organization-member-email"
                        type="email"
                        value={memberEmail}
                        onChange={(event) => setMemberEmail(event.target.value)}
                        disabled={memberPending}
                      />
                      <select
                        aria-label="New member role"
                        value={memberRole}
                        onChange={(event) => setMemberRole(event.target.value as "admin" | "member")}
                        disabled={memberPending}
                      >
                        <option value="member">member</option>
                        <option value="admin">admin</option>
                      </select>
                      <button type="submit" disabled={memberPending || !memberEmail.trim()}>
                        {memberPending ? "Adding…" : "Add member"}
                      </button>
                    </form>
                    <h4>Group workspaces</h4>
                    <ul className="organization-workspaces">
                      {organization.workspaces.map((workspace) => (
                        <li key={workspace.id}>
                          <span>{workspace.name}</span>
                          <button
                            type="button"
                            disabled={workspaceActionPending === workspace.id}
                            onClick={() => void deleteGroupWorkspace(workspace.id, workspace.name)}
                          >
                            Delete
                          </button>
                        </li>
                      ))}
                    </ul>
                    <form className="account-organization-form" onSubmit={(event) => void createGroupWorkspace(event)}>
                      <label htmlFor="organization-workspace-name">Group workspace name</label>
                      <input
                        id="organization-workspace-name"
                        value={workspaceName}
                        onChange={(event) => setWorkspaceName(event.target.value)}
                        maxLength={120}
                        disabled={workspacePending}
                      />
                      <button type="submit" disabled={workspacePending || !workspaceName.trim()}>
                        {workspacePending ? "Creating…" : "Create group workspace"}
                      </button>
                    </form>
                    <button type="button" className="account-action" onClick={() => void loadOrganization()} disabled={organizationLoading}>
                      Refresh organization
                    </button>
                  </>
                )}
                {!organizationLoading && !organization && !organizationError && (
                  <button type="button" className="account-action" onClick={() => void loadOrganization()}>
                    Load organization
                  </button>
                )}
              </section>
            </>
          ) : (
            <form className="account-organization-form" onSubmit={(event) => void createOrganization(event)}>
              <p>Create an organization account to enable group workspaces and Workspace & Access.</p>
              <label htmlFor="organization-name">Organization name</label>
              <input
                id="organization-name"
                value={organizationInput}
                onChange={(event) => setOrganizationInput(event.target.value)}
                maxLength={120}
                disabled={organizationPending}
              />
              <button type="submit" disabled={organizationPending || !organizationInput.trim()}>
                {organizationPending ? "Creating…" : "Create organization account"}
              </button>
            </form>
          )}
          {organizationMessage && (
            <p className="account-message" role="status">{organizationMessage}</p>
          )}
        </section>
      )}
      {accountExportEnabled ? (
      <section className="account-deletion" aria-labelledby="account-export-title">
        <h3 id="account-export-title">Export account data</h3>
        <p className="account-deletion-scope">
          Creates a private ZIP of your account profile and personal workspace data.
          Shared/group workspaces are not included. The export and its single-use
          download grant expire within 24 hours.
        </p>
        <div className="account-deletion-actions">
          <button type="button" disabled={exportPending} onClick={() => void createAccountExport()}>
            {exportPending ? "Working…" : "Create export"}
          </button>
          {exportJob?.grant_token && (
            <button type="button" disabled={exportPending} onClick={() => void downloadAccountExport()}>
              Download export
            </button>
          )}
          {exportJob && (
            <button type="button" disabled={exportPending} onClick={() => void revokeAccountExport()}>
              Revoke export
            </button>
          )}
        </div>
        {exportJob?.expires_at && (
          <p className="account-deletion-scope">Expires: {new Date(exportJob.expires_at).toLocaleString()}</p>
        )}
        {exportMessage && <p className="account-message" role="status">{exportMessage}</p>}
      </section>
      ) : null}
      <section className="account-deletion" aria-labelledby="connectors-title">
        <h3 id="connectors-title">Connected sources</h3>
        <p className="account-deletion-scope">
          Connect a read-only Google Drive source, then run a bounded sync. Documents are stored
          in your workspace connector store; no files are written back to Drive.
        </p>
        <div className="account-deletion-actions">
          {googleDriveConnectorEnabled ? (
            <a href="/api/connectors/google-drive/start">Connect Google Drive</a>
          ) : (
            <span>Google Drive · Coming soon</span>
          )}
          <button type="button" disabled={connectorPending} onClick={() => void loadConnectors()}>
            Refresh sources
          </button>
          {connectors.map((connection) => (
            <span key={connection.connection_id}>
              <button type="button" disabled={connectorPending} onClick={() => void syncConnector(connection.connection_id)}>
                Sync {connection.provider === "google_drive" ? "Drive" : connection.provider}
              </button>
              <button type="button" disabled={connectorPending} onClick={() => void disconnectConnector(connection.connection_id)}>
                Disconnect
              </button>
            </span>
          ))}
        </div>
        {connectors.length === 0 && <p className="account-deletion-scope">No source is connected.</p>}
        {connectors.map((connection) => (
          <p key={`${connection.connection_id}-status`} className="account-deletion-scope">
            {connection.provider}: {connection.status}
            {connection.account_email ? ` (${connection.account_email})` : ""}
            {connection.last_sync_at ? ` · last sync ${new Date(connection.last_sync_at).toLocaleString()}` : ""}
          </p>
        ))}
        {connectorMessage && <p className="account-message" role="status">{connectorMessage}</p>}
      </section>
      {deletionRequestsEnabled ? (
      <section className="account-deletion" aria-labelledby="account-deletion-title">
        <h3>Personal workspace deletion</h3>
        <p className="account-deletion-scope">
          Delete the data in your personal workspace, or the workspace itself. Your account remains.
        </p>
        <div className="account-deletion-actions">
          <button
            type="button"
            disabled={personalDeletionPending || pending}
            onClick={() => void deletePersonalData()}
          >
            {personalDeletionPending ? "Deleting…" : "Delete personal workspace data"}
          </button>
          <button
            type="button"
            disabled={personalDeletionPending || pending}
            onClick={() => void deletePersonalWorkspace()}
          >
            Delete personal workspace
          </button>
        </div>
        {personalDeletionMessage && (
          <p className="account-message" role="status">{personalDeletionMessage}</p>
        )}
        <h3 id="account-deletion-title">Delete account</h3>
        <p className="account-deletion-scope">
          This deletes your personal workspace and its data, snapshots, stored files,
          memberships, and sign-in identity. Shared workspace content owned by other
          members is not deleted. Accepting a request blocks access immediately and
          cannot be cancelled.
        </p>
        {deletion ? (
          <div className="account-deletion-state">
            <p>
              Request ID: <code>{deletion.request_id}</code>{" "}
              <button
                type="button"
                onClick={() => void globalThis.navigator?.clipboard?.writeText(deletion.request_id)}
              >
                Copy request ID
              </button>
            </p>
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
          <button
            type="button"
            disabled={deletionPending}
            onClick={() => void loadCurrentDeletion()}
          >
            Load my current request
          </button>
        </div>
        {deletionMessage ? <p className="account-message" role="alert">{deletionMessage}</p> : null}
      </section>
      ) : null}
    </section>
  );
}
