"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createSupabaseBrowserClient } from "../../lib/supabase/client";
import type { WorkspaceTaskContextResult } from "../../lib/evidence-api";
import { recordTaskTelemetry } from "../../lib/task-telemetry";
import ConnectorSources from "../../components/connector-sources";
import PageTitle from "../../components/page-title";
import RemoteMcp from "../../components/remote-mcp";
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
}: {
  userId: string;
  email: string;
}) {
  const router = useRouter();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceIdCopied, setWorkspaceIdCopied] = useState(false);
  const [records, setRecords] = useState<EvidenceRecord[]>([]);
  const [recordsState, setRecordsState] = useState<LoadState>("ready");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuToggleRef = useRef<HTMLButtonElement>(null);
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
  useEffect(() => () => taskContextAbortRef.current?.abort(), []);
  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      menuToggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);
  const [sourceContent, setSourceContent] = useState("");
  const [sourceDigest, setSourceDigest] = useState("");
  const [sourceMessage, setSourceMessage] = useState("");
  const [sourcePending, setSourcePending] = useState(false);
  const [task, setTask] = useState("");
  const [expectedOutcome, setExpectedOutcome] = useState("");
  const [contextRequirements, setContextRequirements] = useState("");
  const [contextBudget, setContextBudget] = useState(1200);
  const [useJev, setUseJev] = useState(false);
  const [taskContextResult, setTaskContextResult] = useState<WorkspaceTaskContextResult | null>(null);
  const [taskContextPending, setTaskContextPending] = useState(false);
  const [taskContextMessage, setTaskContextMessage] = useState("");
  const taskContextAbortRef = useRef<AbortController | null>(null);
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
      await createSupabaseBrowserClient().auth.getSession();
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
      await createSupabaseBrowserClient().auth.getSession();
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
      await createSupabaseBrowserClient().auth.getSession();
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

  async function prepareTaskContext(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !task.trim() || !expectedOutcome.trim()) return;
    taskContextAbortRef.current?.abort();
    const controller = new AbortController();
    taskContextAbortRef.current = controller;
    const startedAt = performance.now();
    setTaskContextPending(true);
    setTaskContextMessage("");
    setTaskContextResult(null);
    try {
      const response = await fetch(
        `/api/evidence/workspaces/${encodeURIComponent(workspaceId)}/task-context`,
        {
          method: "POST",
          cache: "no-store",
          signal: controller.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task: task.trim(),
            expected_outcome: expectedOutcome.trim(),
            context_requirements: contextRequirements.trim(),
            top_k: 5,
            max_tokens: contextBudget,
            max_chars: contextBudget * 5,
            use_jev: useJev,
          }),
        },
      );
      if (taskContextAbortRef.current !== controller) return;
      if (!response.ok) {
        setTaskContextMessage(
          await readError(response, "Evidence context could not be prepared."),
        );
        return;
      }
      const result = (await response.json()) as WorkspaceTaskContextResult;
      if (taskContextAbortRef.current !== controller) return;
      if (
        result.retrieval?.workspace_id !== workspaceId ||
        !Array.isArray(result.retrieval.evidence) ||
        !Array.isArray(result.relationships)
      ) {
        setTaskContextMessage("The evidence service returned an invalid task context.");
        return;
      }
      setTaskContextResult(result);
      recordTaskTelemetry({
        recordedAt: new Date().toISOString(),
        status: result.retrieval.status,
        latencyMs: Math.max(0, Math.round(performance.now() - startedAt)),
        usedTokens: result.retrieval.used_tokens,
        usedChars: result.retrieval.used_chars,
        selectedCount: result.retrieval.selected_count,
        chunkCount: result.retrieval.chunk_count,
        matchedChunkCount: result.retrieval.matched_chunk_count,
        relationshipCount: result.relationships.length,
        scoringProvider: result.retrieval.scoring_provider,
        fallbackReason: result.retrieval.scoring_fallback_reason ?? null,
        topK: result.retrieval.top_k,
        maxTokens: result.retrieval.max_tokens,
        maxChars: result.retrieval.max_chars,
        useJev,
      });
      setTaskContextMessage(
        result.retrieval.selected_count > 0
          ? "Evidence context is ready to review."
          : "No matching evidence was found. Adjust the task or context requirements.",
      );
    } catch {
      if (!controller.signal.aborted) {
        setTaskContextMessage("Evidence service is unavailable.");
      }
    } finally {
      if (taskContextAbortRef.current === controller) {
        taskContextAbortRef.current = null;
        setTaskContextPending(false);
      }
    }
  }

  function invalidateTaskContext() {
    taskContextAbortRef.current?.abort();
    taskContextAbortRef.current = null;
    setTaskContextPending(false);
    setTaskContextResult(null);
    setTaskContextMessage("");
  }

  async function copyTaskContext() {
    if (!taskContextResult) return;
    const { retrieval, relationships } = taskContextResult;
    const evidenceText = retrieval.evidence.map((item, index) =>
      [
        `Evidence ${index + 1} — ${item.source_label || item.title || item.evidence_id}`,
        `ID: ${item.evidence_id}`,
        item.source_id && item.source_version_id
          ? `Verified source version: ${item.source_id}@${item.source_version_id}`
          : "No exact verified source version is linked.",
        item.content,
      ].join("\n"),
    );
    const relationshipText = relationships.map((item) =>
      `${item.relation_type} (evidence ${item.evidence_id_a} linked to ${item.evidence_id_b}): ${item.rationale}\nBasis: ${item.provenance_note}`,
    );
    const text = [
      `Task:\n${taskContextResult.task}`,
      `Expected outcome:\n${taskContextResult.expected_outcome}`,
      taskContextResult.context_requirements
        ? `Context requirements:\n${taskContextResult.context_requirements}`
        : "",
      "Selected evidence:",
      ...evidenceText,
      relationshipText.length ? `Documented relationships:\n${relationshipText.join("\n\n")}` : "",
      `Evidence snippets only: ${retrieval.used_tokens} estimated tokens (${retrieval.token_estimator}); excludes the task brief and downstream model prompt.`,
      "Use only the supplied evidence for factual claims; identify gaps instead of inventing details.",
    ].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setTaskContextMessage("Task and evidence context copied for your agent.");
    } catch {
      setTaskContextMessage("The task context could not be copied. Select and copy the evidence manually.");
    }
  }

  async function copyWorkspaceId() {
    if (!selectedWorkspace) return;
    try {
      await navigator.clipboard.writeText(selectedWorkspace.id);
      setWorkspaceIdCopied(true);
    } catch {
      setWorkspaceIdCopied(false);
    }
  }

  async function signOut() {
    setPending(true);
    try {
      const { error } = await createSupabaseBrowserClient().auth.signOut({ scope: "local" });
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
        <button
          ref={menuToggleRef}
          type="button"
          className="workspace-menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="workspace-menu"
          onClick={() => setMenuOpen((value) => !value)}
        >
          {menuOpen ? "Close" : "Menu"}
        </button>
        <div className={`workspace-menu${menuOpen ? " workspace-menu--open" : ""}`} id="workspace-menu">
          <nav aria-label="Workspace navigation">
            <Link aria-current="page" href="/workspace" onClick={() => setMenuOpen(false)}>Workspace</Link>
            <Link href="/workspace/telemetry" onClick={() => setMenuOpen(false)}>Telemetry</Link>
            <Link href="/#explorer" onClick={() => setMenuOpen(false)}>Evidence</Link>
            <Link href="/relationships" onClick={() => setMenuOpen(false)}>Relationships</Link>
            <Link href="/workspace#agent-connection" onClick={() => setMenuOpen(false)}>Connect an agent</Link>
          </nav>
          <div className="workspace-user" id="workspace-session">
            <span>{email}</span>
            <button type="button" onClick={signOut} disabled={pending}>Sign out</button>
          </div>
        </div>
      </header>

      <div className="workspace-main">
        <div className="workspace-title-row">
          <div>
            <p className="eyebrow">Authenticated product workspace</p>
            <h1><PageTitle>Evidence workspace</PageTitle></h1>
            <p className="workspace-lede">
              Connect sources, prepare evidence, then give your agent bounded context through API or MCP.
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
                onChange={(event) => {
                  setWorkspaceId(event.target.value);
                  invalidateTaskContext();
                }}
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
                  <div className="workspace-id-row">
                    <strong>Workspace ID:</strong>
                    <code>{selectedWorkspace.id}</code>
                    <button type="button" className="workspace-id-copy" onClick={() => void copyWorkspaceId()}>
                      {workspaceIdCopied ? "Copied" : "Copy"}
                    </button>
                  </div>
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

        {selectedWorkspace && (
          <section className="workspace-panel" aria-labelledby="workflow-heading">
            <div className="workspace-section-heading">
              <div>
                <p className="eyebrow">The product workflow</p>
                <h2 id="workflow-heading">From source to agent task</h2>
              </div>
            </div>
            <ol className="workspace-workflow">
              <li><a href="#connected-sources"><strong>1. Connect and sync sources</strong><span>Connector sync currently imports into your personal workspace.</span></a></li>
              <li><a href="#records-heading"><strong>2. Review evidence</strong><span>Inspect records and source labels in the selected workspace.</span></a></li>
              <li><a href="#task-context"><strong>3. Define a task and prepare context</strong><span>EL selects bounded evidence and explicit links.</span></a></li>
              <li><a href="#agent-connection"><strong>4. Connect your agent</strong><span>The agent uses the task brief and evidence bundle.</span></a></li>
              <li><a href="#task-measurement"><strong>5. Measure outcomes</strong><span>Task-level usage and quality tracking is not connected yet.</span></a></li>
            </ol>
          </section>
        )}

        <div id="connected-sources">
          <ConnectorSources />
        </div>

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
              In your personal workspace, synced connector documents appear as evidence records
              with a source label or path. That label is distinct from an exact, verified source
              snapshot link.
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
                      <p className="workspace-record-content">{record.content}</p>
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
                <p className="eyebrow">Advanced provenance</p>
                <h2 id="source-read-heading">Verified source snapshots</h2>
              </div>
              <span className="workspace-role">Access checked by service</span>
            </div>
            <p className="workspace-note">
              These integrity-verified snapshots are separate from connector sync. This index
              lists the versions your account may see in this workspace. Reading a version
              re-checks active membership, the exact version, and the
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

        {selectedWorkspace && (
          <section id="task-context" className="workspace-panel" aria-labelledby="task-context-heading">
            <div className="workspace-section-heading">
              <div>
                <p className="eyebrow">Task-focused retrieval</p>
                <h2 id="task-context-heading">Define a task and prepare its evidence context</h2>
              </div>
              <span className="workspace-role">Read-only · bounded context</span>
            </div>
            <p className="workspace-note">
              Describe the job, what a successful result should contain, and any source or detail
              to prioritize. EL uses all three as lexical search input and favors directly linked
              records that also match. No external model is called for retrieval; your agent still
              performs the task.
            </p>
            <form className="workspace-form" onSubmit={(event) => void prepareTaskContext(event)}>
              <label htmlFor="task-description">What should the agent do?</label>
              <textarea
                id="task-description"
                required
                maxLength={2000}
                value={task}
                onChange={(event) => {
                  setTask(event.target.value);
                  invalidateTaskContext();
                }}
                placeholder="For example: assess the rollout plan for operational risks"
              />
              <label htmlFor="task-expected-outcome">What should a successful result include?</label>
              <textarea
                id="task-expected-outcome"
                required
                maxLength={2000}
                value={expectedOutcome}
                onChange={(event) => {
                  setExpectedOutcome(event.target.value);
                  invalidateTaskContext();
                }}
                placeholder="For example: list evidence-backed risks, mitigations, and unresolved questions"
              />
              <label htmlFor="task-context-requirements">What context should EL prioritize? (optional)</label>
              <textarea
                id="task-context-requirements"
                maxLength={2000}
                value={contextRequirements}
                onChange={(event) => {
                  setContextRequirements(event.target.value);
                  invalidateTaskContext();
                }}
                placeholder="Name relevant systems, time periods, source paths, or constraints"
              />
              <label className="workspace-checkbox">
                <input
                  type="checkbox"
                  checked={useJev}
                  onChange={(event) => {
                    setUseJev(event.target.checked);
                    invalidateTaskContext();
                  }}
                />
                Score with JEV for this request (up to 50 candidates)
              </label>
              <p className="workspace-note">
                If selected, the task query and candidate evidence snippets are sent to JEV for
                scoring. The server may disable this option; any scoring failure falls back to
                lexical retrieval. JEV provider token usage and cost are not reported yet.
              </p>
              <label htmlFor="task-context-budget">Maximum evidence context</label>
              <select
                id="task-context-budget"
                value={contextBudget}
                onChange={(event) => {
                  setContextBudget(Number(event.target.value));
                  invalidateTaskContext();
                }}
              >
                <option value={800}>800 tokens</option>
                <option value={1200}>1,200 tokens</option>
                <option value={2000}>2,000 tokens</option>
                <option value={4000}>4,000 tokens</option>
              </select>
              <div className="workspace-form-actions">
                <button type="submit" disabled={taskContextPending || recordsState !== "ready"}>
                  {taskContextPending ? "Preparing evidence context…" : "Prepare evidence context"}
                </button>
              </div>
            </form>
            {taskContextMessage && (
              <p className="workspace-message" role="status">{taskContextMessage}</p>
            )}
            {taskContextResult && (
              <div className="task-context-result" aria-live="polite">
                <div className="workspace-section-heading">
                  <div>
                    <h3>Prepared context</h3>
                    <p className="workspace-note">
                      {taskContextResult.retrieval.selected_count} evidence snippets ·{" "}
                      {taskContextResult.retrieval.used_tokens} estimated evidence tokens (task brief excluded) ·{" "}
                      {taskContextResult.retrieval.used_chars} characters ·{" "}
                      {taskContextResult.retrieval.token_estimator}
                    </p>
                    <p className="workspace-note">
                      Ranking: {taskContextResult.retrieval.scoring_provider}
                      {taskContextResult.retrieval.scoring_fallback_reason
                        ? ` (fallback: ${taskContextResult.retrieval.scoring_fallback_reason})`
                        : ""}
                    </p>
                  </div>
                  <button type="button" onClick={() => void copyTaskContext()}>
                    Copy task and evidence for agent
                  </button>
                </div>
                {taskContextResult.retrieval.evidence.length === 0 ? (
                  <p>No evidence was selected. No conclusions are inferred from an empty context.</p>
                ) : (
                  <ol className="task-context-evidence">
                    {taskContextResult.retrieval.evidence.map((item) => (
                      <li key={item.chunk_id}>
                        <div>
                          <strong>{item.source_label || item.title || "Evidence record"}</strong>
                          <span>{item.token_count} tokens · score {item.score.toFixed(3)}</span>
                        </div>
                        <p>{item.content}</p>
                        <code>Evidence ID: {item.evidence_id}</code>
                        <small>
                          {item.source_id && item.source_version_id
                            ? `Verified source version ${item.source_id.slice(0, 8)}… / ${item.source_version_id.slice(0, 8)}…`
                            : "No exact verified source version is linked"}
                        </small>
                      </li>
                    ))}
                  </ol>
                )}
                {taskContextResult.relationships.length > 0 && (
                  <section aria-labelledby="task-context-relationships-heading">
                    <h4 id="task-context-relationships-heading">Explicit links in this context</h4>
                    <ul className="task-context-relationships">
                      {taskContextResult.relationships.map((relationship) => (
                        <li key={relationship.id}>
                          <strong>
                            {relationship.relation_type.replace("_", " ")} · evidence{" "}
                            {relationship.evidence_id_a.slice(0, 8)} links to{" "}
                            {relationship.evidence_id_b.slice(0, 8)}
                          </strong>
                          <span>{relationship.rationale}</span>
                          <small>Basis: {relationship.provenance_note}</small>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                <details>
                  <summary>Show the retrieval query</summary>
                  <pre>{taskContextResult.retrieval.query}</pre>
                </details>
              </div>
            )}
          </section>
        )}

        <section id="agent-connection" className="workspace-agent-access" aria-labelledby="agent-connection-heading">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Agent access</p>
              <h2 id="agent-connection-heading">Connect your agent</h2>
            </div>
          </div>
          <p className="workspace-note">
            Evidence Layer returns selected evidence; your agent or model performs the task and
            writes the answer.
          </p>
          <PersonalApiKeys workspaces={workspaces} />
          <RemoteMcp workspace={selectedWorkspace ? { id: selectedWorkspace.id, name: selectedWorkspace.name } : null} />
        </section>

        <section id="task-measurement" className="workspace-panel" aria-labelledby="task-measurement-heading">
          <div className="workspace-section-heading">
            <div>
              <p className="eyebrow">Not connected yet</p>
              <h2 id="task-measurement-heading">Task outcomes and usage</h2>
            </div>
            <span className="workspace-role">No live measurements</span>
          </div>
          <p className="workspace-note">
            When enabled, API and MCP can retrieve bounded context, but this workspace does not yet run agent
            tasks or collect the model's token usage, cost, latency, or task success. Existing
            reports are historical experiments, not measurements for this workspace.
          </p>
          <div className="workspace-form-actions">
            <Link href="/evaluation">Historical evaluation reports</Link>
            <Link href="/cost-performance">Planning estimates</Link>
          </div>
        </section>

        <details className="workspace-advanced">
          <summary>Full capability reference (advanced)</summary>
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
        </details>

        <footer className="workspace-footer">
          <span>Workspace data is scoped to verified membership and service permissions.</span>
          <Link href="/documentation">Developer documentation</Link>
        </footer>
      </div>
    </main>
  );
}
