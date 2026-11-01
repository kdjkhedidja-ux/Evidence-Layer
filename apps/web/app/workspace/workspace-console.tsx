"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { createSupabaseBrowserClient } from "../../lib/supabase/client";

type Workspace = { id: string; name: string; role: "owner" | "member" };
type EvidenceRecord = {
  id: string;
  workspace_id: string;
  content: string;
  source_label: string | null;
  created_at: string;
  updated_at: string;
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

export default function WorkspaceConsole({
  userId,
  email,
}: {
  userId: string;
  email: string;
}) {
  const router = useRouter();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [records, setRecords] = useState<EvidenceRecord[]>([]);
  const [recordsState, setRecordsState] = useState<LoadState>("ready");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deleteConfirmationId, setDeleteConfirmationId] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [sourceContent, setSourceContent] = useState("");
  const [sourceDigest, setSourceDigest] = useState("");
  const [sourceMessage, setSourceMessage] = useState("");
  const [sourcePending, setSourcePending] = useState(false);
  const [label, setLabel] = useState("");
  const [content, setContent] = useState("");

  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.id === workspaceId) ?? null,
    [workspaceId, workspaces],
  );

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
          (candidate.role === "owner" || candidate.role === "member")
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

  useEffect(() => {
    void loadEvidence(workspaceId);
    setMessage("");
    setEditingId(null);
    setDeleteConfirmationId(null);
    setSourceContent("");
    setSourceDigest("");
    setSourceMessage("");
  }, [loadEvidence, workspaceId]);

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
      const response = await fetch(path, {
        method,
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content.trim(),
          source_label: label.trim() || null,
        }),
      });
      if (!response.ok) {
        setMessage(await readError(response, "Evidence could not be saved."));
        return;
      }
      setContent("");
      setLabel("");
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

  async function readVerifiedSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !UUID.test(sourceId) || !UUID.test(versionId)) {
      setSourceMessage("Enter valid source and version IDs.");
      return;
    }
    setSourcePending(true);
    setSourceMessage("");
    setSourceContent("");
    setSourceDigest("");
    const path = "/api/evidence/workspaces/" + encodeURIComponent(workspaceId) +
      "/sources/" + encodeURIComponent(sourceId) +
      "/versions/" + encodeURIComponent(versionId) + "/content";
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

  return (
    <main className="workspace-page">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <nav aria-label="Workspace navigation">
          <Link aria-current="page" href="/workspace">Workspace</Link>
          <Link href="/">Demo / Synthetic</Link>
          <Link href="/documentation">Documentation</Link>
        </nav>
        <div className="workspace-user">
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
              <p>
                Memberships come from verified workspace access. Invitations, role changes, and membership administration are not available in the current service contract.
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

        {selectedWorkspace && (
          <section className="workspace-panel" aria-labelledby="records-heading">
            <div className="workspace-section-heading">
              <div>
                <p className="eyebrow">Workspace scoped · RLS protected</p>
                <h2 id="records-heading">Evidence records</h2>
              </div>
              <span className="workspace-count">{records.length} records</span>
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
                    </article>
                  ))}
                </section>
                <form className="workspace-form" onSubmit={saveEvidence}>
                  <h3 id="evidence-editor-title" tabIndex={-1}>
                    {editingId ? "Edit evidence record" : "Add evidence record"}
                  </h3>
                  <label htmlFor="evidence-source-label">Source label (optional)</label>
                  <input
                    id="evidence-source-label"
                    maxLength={512}
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                  />
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
                </form>
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
                <h2 id="source-read-heading">Read a verified source version</h2>
              </div>
              <span className="workspace-role">Access checked by service</span>
            </div>
            <p className="workspace-note">
              The Evidence Service checks active workspace membership, the exact source version, and the current source grant before opening content and again before delivery. Denials and service failures return no source content.
            </p>
            <form className="workspace-source-form" onSubmit={readVerifiedSource}>
              <label htmlFor="source-id">Source ID</label>
              <input
                id="source-id"
                autoComplete="off"
                inputMode="text"
                pattern="[0-9a-fA-F-]{36}"
                required
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
              />
              <label htmlFor="source-version-id">Version ID</label>
              <input
                id="source-version-id"
                autoComplete="off"
                inputMode="text"
                pattern="[0-9a-fA-F-]{36}"
                required
                value={versionId}
                onChange={(event) => setVersionId(event.target.value)}
              />
              <button type="submit" disabled={sourcePending}>
                {sourcePending ? "Checking access…" : "Read source"}
              </button>
            </form>
            {sourceMessage && <p className="workspace-message" role="status">{sourceMessage}</p>}
            {sourceContent && (
              <div className="workspace-source-result">
                <p><strong>Verified content digest:</strong> <code>{sourceDigest}</code></p>
                <pre>{sourceContent}</pre>
              </div>
            )}
          </section>
        )}

        <section aria-labelledby="capabilities-heading" className="workspace-panel">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Feature availability</p>
              <h2 id="capabilities-heading">Connected and pending capabilities</h2>
            </div>
          </div>
          <div className="workspace-capability-grid">
            <Capability title="Authentication" state="Connected" detail="Supabase Auth session is verified server-side; sign out revokes the browser session." />
            <Capability title="Workspace & access" state="Connected" detail="Membership list and workspace-scoped requests use the authenticated user's existing RLS policies." />
            <Capability title="Evidence records" state="Connected" detail="List, create, edit, and delete use the existing Evidence Service contract and user JWT." />
            <Capability title="Source relationships" state="Demo only" detail="Relationship links currently exist only in the labelled synthetic fixture." />
            <Capability title="Reading guide" state="Demo only" detail="The current guide is fixture-authored; no live guide API is available." />
            <Capability title="Verification" state="Read-time only" detail="Source reads use verified snapshots and access checks. Verification history/status has no frontend API." />
            <Capability title="Evaluation" state="Historical" detail="Published benchmark outputs are fixed reports, not per-workspace live evaluation." />
            <Capability title="Cost & performance" state="Historical estimates" detail="Benchmark estimates are available; workspace usage metering and invoices are not exposed." />
            <Capability title="Integrations" state="Unavailable" detail="No integration-management endpoint is exposed by the current service contract." />
            <Capability title="Settings & access" state="Read only" detail="Membership roles can be viewed; invitations, role changes, and access administration are not implemented." />
          </div>
        </section>

        <footer className="workspace-footer">
          <span>Live workspace data is separate from the demo fixture.</span>
          <Link href="/cost-performance">View benchmark reports and their limits</Link>
        </footer>
      </div>
    </main>
  );
}

function Capability({
  title,
  state,
  detail,
}: {
  title: string;
  state: string;
  detail: string;
}) {
  return (
    <article className="workspace-capability">
      <div><h3>{title}</h3><span>{state}</span></div>
      <p>{detail}</p>
    </article>
  );
}
