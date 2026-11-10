"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { createSupabaseBrowserClient } from "../../lib/supabase/client";
import AccountProfile from "./account-profile";
import PersonalApiKeys from "./personal-api-keys";
import { CAPABILITY_GROUPS } from "./capability-catalog";

type Workspace = { id: string; name: string; role: "owner" | "admin" | "member"; workspace_type?: "personal" | "group" };
type EvidenceRecord = {
  id: string;
  workspace_id: string;
  content: string;
  source_label: string | null;
  source_id: string | null;
  source_version_id: string | null;
  created_at: string;
  updated_at: string;
};
type SourceVersion = {
  source_id: string;
  version_id: string;
  state: "staged" | "published";
  sha256: string;
  size_bytes: number;
  created_at: string;
};
type LoadState = "loading" | "ready" | "expired" | "unavailable";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    return typeof body.error === "string" ? body.error : fallback;
  } catch {
    return fallback;
  }
}

function dateLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function formatBytes(value: number): string {
  if (!Number.isSafeInteger(value) || value < 0) return "Size unavailable";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function WorkspaceConsole({
  userId,
  email,
  sessionExpiresAt = null,
}: {
  userId: string;
  email: string;
  sessionExpiresAt?: number | null;
}) {
  const router = useRouter();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [records, setRecords] = useState<EvidenceRecord[]>([]);
  const [recordsState, setRecordsState] = useState<LoadState>("ready");
  const [message, setMessage] = useState("");
  const [sessionMessage, setSessionMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deleteConfirmationId, setDeleteConfirmationId] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceVersion[]>([]);
  const [sourcesState, setSourcesState] = useState<LoadState>("ready");
  const [readingVersionId, setReadingVersionId] = useState<string | null>(null);
  const [expandedCapabilityGroups, setExpandedCapabilityGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!editingId) return;
    const editor = document.getElementById("evidence-editor");
    editor?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [editingId]);
  const [sourceContent, setSourceContent] = useState("");
  const [sourceDigest, setSourceDigest] = useState("");
  const [sourceMessage, setSourceMessage] = useState("");
  const [sourcePending, setSourcePending] = useState(false);
  const [label, setLabel] = useState("");
  const [content, setContent] = useState("");
  const [linkKey, setLinkKey] = useState("");

  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.id === workspaceId) ?? null,
    [workspaceId, workspaces],
  );

  const publishedVersions = useMemo(
    () => sources.filter((version) => version.state === "published"),
    [sources],
  );

  const sourcesById = useMemo(() => {
    const grouped = new Map<string, SourceVersion[]>();
    for (const version of sources) {
      const list = grouped.get(version.source_id) ?? [];
      list.push(version);
      grouped.set(version.source_id, list);
    }
    return [...grouped.entries()];
  }, [sources]);

  const loadWorkspaces = useCallback(async (): Promise<Workspace[] | null> => {
    setLoadState("loading");
    try {
      const response = await fetch("/api/evidence/me", { cache: "no-store" });
      if (response.status === 401) {
        setLoadState("expired");
        return null;
      }
      if (!response.ok) {
        setLoadState("unavailable");
        return null;
      }
      const payload = (await response.json()) as {
        user_id?: unknown;
        workspaces?: unknown;
      };
      if (payload.user_id !== userId || !Array.isArray(payload.workspaces)) {
        setWorkspaces([]);
        setWorkspaceId("");
        setLoadState("expired");
        router.refresh();
        return null;
      }
      const memberships = payload.workspaces.filter((item): item is Workspace => {
        if (!item || typeof item !== "object") return false;
        const candidate = item as Record<string, unknown>;
        return (
          typeof candidate.id === "string" &&
          UUID.test(candidate.id) &&
          typeof candidate.name === "string" &&
          (candidate.role === "owner" || candidate.role === "admin" || candidate.role === "member")
        );
      });
      setWorkspaces(memberships);
      setWorkspaceId((current) =>
        memberships.some((workspace) => workspace.id === current)
          ? current
          : memberships[0]?.id ?? "",
      );
      setLoadState("ready");
      return memberships;
    } catch {
      setLoadState("unavailable");
      return null;
    }
  }, [router, userId]);

  const loadEvidence = useCallback(async (selectedId: string) => {
    if (!selectedId) {
      setRecords([]);
      setRecordsState("ready");
      return;
    }
    setRecordsState("loading");
    try {
      const response = await fetch(
        `/api/evidence/workspaces/${encodeURIComponent(selectedId)}/evidence`,
        { cache: "no-store" },
      );
      if (response.status === 401) {
        setRecords([]);
        setRecordsState("expired");
        router.refresh();
        return;
      }
      if (!response.ok) {
        setRecords([]);
        setRecordsState("unavailable");
        setMessage(await readError(response, "Evidence records could not be loaded."));
        return;
      }
      const payload = (await response.json()) as unknown;
      if (
        !Array.isArray(payload) ||
        payload.some((record) =>
          !record ||
          typeof record !== "object" ||
          (record as EvidenceRecord).workspace_id !== selectedId ||
          typeof (record as EvidenceRecord).id !== "string" ||
          typeof (record as EvidenceRecord).content !== "string",
        )
      ) {
        setRecords([]);
        setRecordsState("unavailable");
        setMessage("The evidence service returned an invalid response.");
        return;
      }
      setRecords(payload as EvidenceRecord[]);
      setRecordsState("ready");
    } catch {
      setRecords([]);
      setRecordsState("unavailable");
      setMessage("Evidence records could not be loaded.");
    }
  }, [router]);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  const loadSources = useCallback(async (selectedId: string) => {
    if (!selectedId) {
      setSources([]);
      setSourcesState("ready");
      return;
    }
    setSourcesState("loading");
    try {
      const response = await fetch(
        `/api/evidence/workspaces/${encodeURIComponent(selectedId)}/sources`,
        { cache: "no-store" },
      );
      if (response.status === 401) {
        setSources([]);
        setSourcesState("expired");
        router.refresh();
        return;
      }
      if (!response.ok) {
        setSources([]);
        setSourcesState("unavailable");
        return;
      }
      const payload = (await response.json()) as unknown;
      if (
        !Array.isArray(payload) ||
        payload.some((item) => {
          if (!item || typeof item !== "object") return true;
          const candidate = item as Record<string, unknown>;
          return (
            typeof candidate.source_id !== "string" ||
            !UUID.test(candidate.source_id) ||
            typeof candidate.version_id !== "string" ||
            !UUID.test(candidate.version_id) ||
            (candidate.state !== "published" && candidate.state !== "staged")
          );
        })
      ) {
        setSources([]);
        setSourcesState("unavailable");
        return;
      }
      setSources(payload as SourceVersion[]);
      setSourcesState("ready");
    } catch {
      setSources([]);
      setSourcesState("unavailable");
    }
  }, [router]);

  useEffect(() => {
    void loadEvidence(workspaceId);
    void loadSources(workspaceId);
    setMessage("");
    setEditingId(null);
    setDeleteConfirmationId(null);
    setLinkKey("");
    setReadingVersionId(null);
    setSourceContent("");
    setSourceDigest("");
    setSourceMessage("");
  }, [loadEvidence, loadSources, workspaceId]);

  useEffect(() => {
    const { data: { subscription } } = createSupabaseBrowserClient().auth.onAuthStateChange(
      (event, session) => {
        if (event === "SIGNED_OUT" || (event === "SIGNED_IN" && session?.user.id !== userId)) {
          setWorkspaces([]);
          setRecords([]);
          setWorkspaceId("");
          setLoadState("expired");
          router.refresh();
        }
      },
    );
    return () => subscription.unsubscribe();
  }, [router, userId]);

  async function createPersonalWorkspace() {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/evidence/personal-workspace", {
        method: "PUT",
        cache: "no-store",
      });
      if (!response.ok) {
        setMessage(await readError(response, "Workspace setup is unavailable."));
        return;
      }
      const created = (await response.json()) as Workspace;
      const memberships = await loadWorkspaces();
      if (memberships?.some((workspace) => workspace.id === created.id)) {
        setWorkspaceId(created.id);
        setMessage("Personal workspace access was confirmed.");
      } else {
        setMessage("Workspace membership could not be confirmed.");
      }
    } catch {
      setMessage("Workspace service is unavailable.");
    } finally {
      setPending(false);
    }
  }

  async function saveEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !content.trim()) return;
    setPending(true);
    setMessage("");
    const method = editingId ? "PATCH" : "POST";
    const path = "/api/evidence/workspaces/" + encodeURIComponent(workspaceId) +
      "/evidence" + (editingId ? `/${encodeURIComponent(editingId)}` : "");
    try {
      const [linkSourceId, linkVersionId] = linkKey ? linkKey.split(":") : [];
      const response = await fetch(path, {
        method,
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content.trim(),
          source_label: label.trim() || null,
          source_id: linkKey ? linkSourceId : null,
          source_version_id: linkKey ? linkVersionId : null,
        }),
      });
      if (!response.ok) {
        setMessage(await readError(response, "Evidence could not be saved."));
        return;
      }
      setContent("");
      setLabel("");
      setLinkKey("");
      setEditingId(null);
      setMessage(editingId ? "Evidence record updated." : "Evidence record created.");
      await loadEvidence(workspaceId);
    } catch {
      setMessage("Evidence service is unavailable.");
    } finally {
      setPending(false);
    }
  }

  function editRecord(record: EvidenceRecord) {
    setEditingId(record.id);
    setContent(record.content);
    setLabel(record.source_label ?? "");
    setLinkKey(
      record.source_id && record.source_version_id
        ? `${record.source_id}:${record.source_version_id}`
        : "",
    );
    setDeleteConfirmationId(null);
    document.getElementById("evidence-editor-title")?.focus();
  }

  async function deleteRecord(recordId: string) {
    if (!workspaceId) return;
    setPending(true);
    setMessage("");
    try {
      const response = await fetch(
        "/api/evidence/workspaces/" + encodeURIComponent(workspaceId) +
          "/evidence/" + encodeURIComponent(recordId),
        { method: "DELETE", cache: "no-store" },
      );
      if (!response.ok) {
        setMessage(await readError(response, "Evidence could not be deleted."));
        return;
      }
      setRecords((current) => current.filter((record) => record.id !== recordId));
      setDeleteConfirmationId(null);
      if (editingId === recordId) {
        setEditingId(null);
        setContent("");
        setLabel("");
      }
      setMessage("Evidence record deleted.");
    } catch {
      setMessage("Evidence service is unavailable.");
    } finally {
      setPending(false);
    }
  }

  async function readSourceVersion(readSourceId: string, readVersionId: string) {
    if (!workspaceId || !UUID.test(readSourceId) || !UUID.test(readVersionId)) {
      setSourceMessage("The selected source version is not valid.");
      return;
    }
    setSourcePending(true);
    setSourceMessage("");
    setSourceContent("");
    setSourceDigest("");
    setReadingVersionId(readVersionId);
    const path = "/api/evidence/workspaces/" + encodeURIComponent(workspaceId) +
      "/sources/" + encodeURIComponent(readSourceId) +
      "/versions/" + encodeURIComponent(readVersionId) + "/content";
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        setSourceMessage(await readError(response, "Verified source content is unavailable."));
        return;
      }
      const bytes = await response.arrayBuffer();
      try {
        setSourceContent(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      } catch {
        setSourceMessage("This verified source version is not UTF-8 text.");
        return;
      }
      setSourceDigest(response.headers.get("content-digest") ?? "Digest not supplied");
      setSourceMessage("Source access was verified for this read.");
    } catch {
      setSourceMessage("Verified source content is unavailable.");
    } finally {
      setSourcePending(false);
    }
  }

  async function signOut() {
    setPending(true);
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

  async function signOutEverywhere() {
    setPending(true);
    setSessionMessage("");
    try {
      const { error } = await createSupabaseBrowserClient().auth.signOut({ scope: "global" });
      if (error) {
        setSessionMessage("Signing out all sessions could not be confirmed. Try again.");
        return;
      }
      router.replace("/login?reason=signed-out");
      router.refresh();
    } catch {
      setSessionMessage("Signing out all sessions could not be confirmed. Try again.");
    } finally {
      setPending(false);
    }
  }

  const sessionExpiryIso = sessionExpiresAt
    ? new Date(sessionExpiresAt * 1000).toISOString()
    : null;
  const sessionExpiry = sessionExpiryIso ? dateLabel(sessionExpiryIso) : null;

  return (
    <main className="workspace-page">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <nav aria-label="Workspace navigation">
          <Link aria-current="page" href="/workspace">Workspace</Link>
          <Link href="/">Evidence Explorer</Link>
          <Link href="/relationships">Relationships</Link>
          <Link href="/documentation">Documentation</Link>
        </nav>
        <div className="workspace-user" id="workspace-session">
          <span>{email}</span>
          <button type="button" onClick={signOut} disabled={pending}>Sign out</button>
        </div>
      </header>

      <div className="workspace-main">
        <div className="workspace-title-row">
          <div>
            <p className="eyebrow">Authenticated product workspace</p>
            <h1>Evidence workspace</h1>
            <p className="workspace-lede">
              Workspace membership and evidence records are loaded through the server API and the existing Evidence Service.
            </p>
          </div>
          <span className="workspace-live-badge"><span aria-hidden="true" /> Live service data</span>
        </div>

        <section className="workspace-panel" aria-labelledby="membership-heading">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Access boundary</p>
              <h2 id="membership-heading">Workspaces and membership</h2>
            </div>
            <span className="workspace-role">{selectedWorkspace?.role ?? "No workspace selected"}</span>
          </div>
          {loadState === "loading" && <p role="status">Checking verified workspace memberships…</p>}
          {loadState === "expired" && (
            <p role="alert">Your verified session is no longer available. <Link href="/login">Sign in again</Link>.</p>
          )}
          {loadState === "unavailable" && (
            <p role="alert">Identity or workspace service is unavailable. No workspace data is shown.</p>
          )}
          {loadState === "ready" && workspaces.length > 0 && (
            <div className="workspace-selector">
              <label htmlFor="workspace-select">Authorized workspace</label>
              <select
                id="workspace-select"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
              >
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name} · {workspace.role}
                  </option>
                ))}
              </select>
              {selectedWorkspace && (
                <>
                  <p><strong>Name:</strong> {selectedWorkspace.name}</p>
                  <p>
                    <strong>Type:</strong>{" "}
                    {selectedWorkspace.workspace_type === "group"
                      ? "Group workspace"
                      : selectedWorkspace.workspace_type === "personal"
                        ? "Personal workspace"
                        : "Not exposed by the membership API"}
                  </p>
                  <p>
                    <strong>Role:</strong>{" "}
                    {selectedWorkspace.role === "owner"
                      ? "Owner"
                      : selectedWorkspace.role === "admin"
                        ? "Admin"
                        : "Member"}
                  </p>
                </>
              )}
              <p>
                Memberships come from verified workspace access. Organization admins manage
                members and roles from{" "}
                <Link href="/?account=1#account-organization-title">Account → Workspace &amp; Access</Link>;
                there is no per-workspace invitation flow or permissions editor.
              </p>
            </div>
          )}
          {loadState === "ready" && workspaces.length === 0 && (
            <div className="workspace-empty">
              <p>No active workspace membership was returned for this verified account.</p>
              <button type="button" onClick={createPersonalWorkspace} disabled={pending}>
                Create personal workspace
              </button>
            </div>
          )}
        </section>

        <AccountProfile email={email} />

        <PersonalApiKeys workspaces={workspaces} />

        <section id="session-management" className="workspace-panel" aria-labelledby="session-heading">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Verified browser session</p>
              <h2 id="session-heading">Session</h2>
            </div>
            <span className="workspace-role">Server verified</span>
          </div>
          <p className="workspace-note">
            Supabase Auth verified this session server-side before workspace data was returned.
            Session inventory and per-device management are not available in the current service
            contract. Signing out everywhere ends every session for this account.
          </p>
          {sessionExpiry && sessionExpiryIso && (
            <p>
              <strong>Session expires:</strong>{" "}
              <time dateTime={sessionExpiryIso}>{sessionExpiry}</time>
            </p>
          )}
          <div className="workspace-form-actions">
            <button type="button" onClick={signOutEverywhere} disabled={pending}>
              {pending ? "Signing out…" : "Sign out everywhere"}
            </button>
          </div>
          <p>
            <Link href="/mfa?next=%2Fworkspace">Manage two-factor authentication</Link>
          </p>
          {sessionMessage && <p className="workspace-message" role="alert">{sessionMessage}</p>}
        </section>

        {selectedWorkspace && (
          <section className="workspace-panel" aria-labelledby="records-heading">
            <div className="workspace-section-heading">
              <div>
                <p className="eyebrow">Workspace scoped · RLS protected</p>
                <h2 id="records-heading">Evidence records</h2>
              </div>
              <span className="workspace-count" aria-live="polite">
                {recordsState === "ready"
                  ? `${records.length} ${records.length === 1 ? "record" : "records"}`
                  : recordsState === "loading"
                    ? "Loading records…"
                    : recordsState === "expired"
                      ? "Session expired"
                      : "Unavailable"}
              </span>
            </div>
            <p className="workspace-note">
              These are stored workspace evidence records and their labels. Snapshot source content is read separately through the verified source boundary below; a signed-in session by itself does not grant source access.
            </p>
            {recordsState === "loading" && <p role="status">Loading authorized evidence records…</p>}
            {recordsState === "expired" && <p role="alert">Your session needs to be verified again.</p>}
            {recordsState === "unavailable" && <p role="alert">{message || "Evidence service is unavailable."}</p>}
            {recordsState === "ready" && (
              <div className="workspace-record-layout">
                <section aria-label="Evidence records" className="workspace-record-list">
                  {records.length === 0 && <p>No evidence records are stored in this workspace.</p>}
                  {records.map((record) => (
                    <article className="workspace-record" key={record.id}>
                      <div className="workspace-record-meta">
                        <span>{record.source_label || "No source label"}</span>
                        <time dateTime={record.updated_at}>Updated {dateLabel(record.updated_at)}</time>
                      </div>
                      <p>{record.content}</p>
                      {record.source_id && record.source_version_id && (
                        <p className="workspace-record-link">
                          Linked to source {record.source_id.slice(0, 8)}… version{" "}
                          {record.source_version_id.slice(0, 8)}…
                          {publishedVersions.some(
                            (version) =>
                              version.source_id === record.source_id &&
                              version.version_id === record.source_version_id,
                          ) && (
                            <button
                              type="button"
                              onClick={() =>
                                record.source_id &&
                                record.source_version_id &&
                                void readSourceVersion(record.source_id, record.source_version_id)
                              }
                              disabled={sourcePending}
                            >
                              Read linked source
                            </button>
                          )}
                        </p>
                      )}
                      {selectedWorkspace.role !== "member" ? (
                        <div className="workspace-record-actions">
                          <button type="button" onClick={() => editRecord(record)} disabled={pending}>
                            Edit
                          </button>
                          {deleteConfirmationId === record.id ? (
                            <>
                              <span>Delete this record?</span>
                              <button
                                type="button"
                                onClick={() => void deleteRecord(record.id)}
                                disabled={pending}
                              >
                                Confirm delete
                              </button>
                              <button
                                type="button"
                                onClick={() => setDeleteConfirmationId(null)}
                                disabled={pending}
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setDeleteConfirmationId(record.id)}
                              disabled={pending}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      ) : <p className="workspace-note">Read-only member access</p>}
                    </article>
                  ))}
                </section>
                {selectedWorkspace.role !== "member" && <form id="evidence-editor" className="workspace-form" onSubmit={saveEvidence}>
                  <h3 id="evidence-editor-title" tabIndex={-1}>
                    {editingId ? "Edit evidence record" : "Add evidence record"}
                  </h3>
                  <p className="workspace-note">
                    Records belong to the selected workspace. Owners and admins can add or edit them;
                    members keep read-only access. Source links are optional and only published
                    versions can be attached.
                  </p>
                  <label htmlFor="evidence-source-label">Source label (optional)</label>
                  <input
                    id="evidence-source-label"
                    maxLength={512}
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                  />
                  <label htmlFor="evidence-source-link">Linked source version (optional)</label>
                  <select
                    id="evidence-source-link"
                    value={linkKey}
                    onChange={(event) => setLinkKey(event.target.value)}
                    disabled={sourcesState === "loading"}
                  >
                    <option value="">No linked source</option>
                    {publishedVersions.map((version) => (
                      <option
                        key={version.version_id}
                        value={`${version.source_id}:${version.version_id}`}
                      >
                        {`Source ${version.source_id.slice(0, 8)}… · version ${version.version_id.slice(0, 8)}… · ${dateLabel(version.created_at)}`}
                      </option>
                    ))}
                  </select>
                  {sourcesState === "ready" && publishedVersions.length === 0 && (
                    <p className="workspace-note">
                      No published source versions are available to link in this workspace.
                    </p>
                  )}
                  <label htmlFor="evidence-content">Evidence content</label>
                  <textarea
                    id="evidence-content"
                    required
                    maxLength={65_536}
                    rows={8}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                  />
                  <div className="workspace-form-actions">
                    <button type="submit" disabled={pending || !content.trim()}>
                      {pending ? "Saving…" : editingId ? "Save changes" : "Add evidence"}
                    </button>
                    {editingId && (
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(null);
                          setContent("");
                          setLabel("");
                        }}
                        disabled={pending}
                      >
                        Cancel edit
                      </button>
                    )}
                  </div>
                </form>}
              </div>
            )}
            {message && <p className="workspace-message" role="status">{message}</p>}
          </section>
        )}

        {selectedWorkspace && (
          <section className="workspace-panel" aria-labelledby="source-read-heading">
            <div className="workspace-section-heading">
              <div>
                <p className="eyebrow">Read-time checks</p>
                <h2 id="source-read-heading">Workspace sources</h2>
              </div>
              <span className="workspace-role">Access checked by service</span>
            </div>
            <p className="workspace-note">
              This index lists the source versions your account may see in this workspace.
              Reading a version re-checks active membership, the exact version, and the
              current source grant before opening content and again before delivery.
              Denials and service failures return no source content.
            </p>
            {sourcesState === "loading" && <p role="status">Loading authorized workspace sources…</p>}
            {sourcesState === "expired" && <p role="alert">Your session needs to be verified again.</p>}
            {sourcesState === "unavailable" && (
              <p role="alert">The source index is unavailable. No source data is shown.</p>
            )}
            {sourcesState === "ready" && sourcesById.length === 0 && (
              <p>No verified sources are available to your account in this workspace.</p>
            )}
            {sourcesState === "ready" && sourcesById.length > 0 && (
              <ul className="workspace-source-list">
                {sourcesById.map(([sourceGroupId, versions]) => (
                  <li key={sourceGroupId}>
                    <h3>Source {sourceGroupId.slice(0, 8)}…</h3>
                    <ul>
                      {versions.map((version) => (
                        <li key={version.version_id} className="workspace-source-version">
                          <span>
                            Version {version.version_id.slice(0, 8)}… ·{" "}
                            {version.state === "published" ? "Published" : "Pending verification"} ·{" "}
                            {formatBytes(version.size_bytes)} · {dateLabel(version.created_at)}
                          </span>
                          {version.state === "published" && (
                            <button
                              type="button"
                              onClick={() =>
                                void readSourceVersion(version.source_id, version.version_id)
                              }
                              disabled={sourcePending}
                            >
                              {sourcePending && readingVersionId === version.version_id
                                ? "Checking access…"
                                : "Read source"}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
            {sourceMessage && <p className="workspace-message" role="status">{sourceMessage}</p>}
            {sourceContent && (
              <div className="workspace-source-result">
                <p><strong>Verified content digest:</strong> <code>{sourceDigest}</code></p>
                <pre>{sourceContent}</pre>
              </div>
            )}
          </section>
        )}

        <section aria-labelledby="capabilities-heading" className="workspace-panel workspace-capability-catalog">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Feature availability</p>
              <h2 id="capabilities-heading">Capability Catalog</h2>
              <p className="workspace-note">
                Availability reflects the existing application and service contract. It does not report live production uptime.
              </p>
            </div>
          </div>
          <div className="workspace-capability-grid">
            {CAPABILITY_GROUPS.map((group, groupIndex) => {
              const groupId = `capability-group-${groupIndex}`;
              const listId = `${groupId}-list`;
              const isExpanded = expandedCapabilityGroups[group.name] ?? false;
              const visibleFeatures = isExpanded ? group.features : group.features.slice(0, 1);
              const hiddenCount = group.features.length - 1;
              return (
                <section
                  aria-labelledby={groupId}
                  className="workspace-capability-group"
                  key={group.name}
                >
                  <div className="workspace-capability-group-head">
                    <h3 id={groupId}>{group.name}</h3>
                    <span className="workspace-capability-total">
                      {group.features.length} {group.features.length === 1 ? "capability" : "capabilities"}
                    </span>
                  </div>
                  <ul className="workspace-capability-list" id={listId}>
                    {visibleFeatures.map((feature) => (
                      <li className="workspace-capability" key={feature.name}>
                        <div className="workspace-capability-heading">
                          <h4>{feature.name}</h4>
                          <span className="workspace-capability-state" data-status={feature.status}>
                            {feature.status}
                          </span>
                        </div>
                        <p>{feature.detail}</p>
                        {feature.href && (
                          <Link href={feature.href} className="workspace-capability-link">
                            Open existing page
                          </Link>
                        )}
                      </li>
                    ))}
                  </ul>
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      className="workspace-capability-toggle"
                      aria-expanded={isExpanded}
                      aria-controls={listId}
                      onClick={() => setExpandedCapabilityGroups((current) => ({
                        ...current,
                        [group.name]: !isExpanded,
                      }))}
                    >
                      {isExpanded ? "Show less" : `Show ${hiddenCount} more`}
                    </button>
                  )}
                </section>
              );
            })}
          </div>
        </section>

        <footer className="workspace-footer">
          <span>Live workspace data comes from the verified Evidence Service.</span>
          <Link href="/cost-performance">View benchmark reports and their limits</Link>
        </footer>
      </div>
    </main>
  );
}
