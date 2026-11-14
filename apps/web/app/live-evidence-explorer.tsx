"use client";

import Link from "next/link";
import RelationshipCanvas from "./relationships/relationship-canvas";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import AuthAccount from "../components/auth-account";
import ThemeToggle from "./theme-toggle";

type Workspace = { id: string; name: string; role: "owner" | "admin" | "member" };
type Evidence = {
  id: string;
  workspace_id: string;
  content: string;
  source_label: string | null;
  source_id: string | null;
  source_version_id: string | null;
  created_at: string;
  updated_at: string;
};
type Relationship = {
  id: string;
  workspace_id: string;
  evidence_id_a: string;
  evidence_id_b: string;
  relation_type: "supports" | "contradicts" | "contextualizes" | "derived_from";
  rationale: string;
  provenance_note: string;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
};
type LoadState = "loading" | "ready" | "unauthenticated" | "unavailable";
type DataState = "idle" | "loading" | "ready" | "unavailable";

const RELATION_LABELS: Record<Relationship["relation_type"], string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  contextualizes: "Contextualizes",
  derived_from: "Derived from",
};

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { error?: unknown };
    return typeof body.error === "string" ? body.error : fallback;
  } catch {
    return fallback;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export default function LiveEvidenceExplorer({
  account,
  relationshipsOnly = false,
}: {
  account: { id: string; email: string } | null;
  relationshipsOnly?: boolean;
}) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceState, setWorkspaceState] = useState<LoadState>("loading");
  const [records, setRecords] = useState<Evidence[]>([]);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [recordsState, setRecordsState] = useState<DataState>("idle");
  const [relationshipsState, setRelationshipsState] = useState<DataState>("idle");
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [provenanceFilter, setProvenanceFilter] = useState<"all" | "linked" | "unlinked">("all");
  const [message, setMessage] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const mobileMenuToggleRef = useRef<HTMLButtonElement>(null);
  const [relationshipDraft, setRelationshipDraft] = useState({
    evidence_id_a: "",
    evidence_id_b: "",
    relation_type: "contextualizes" as Relationship["relation_type"],
    rationale: "",
    provenance_note: "",
  });
  const [editingRelationship, setEditingRelationship] = useState("");
  const [confirmDeleteRelationshipId, setConfirmDeleteRelationshipId] = useState("");
  const [editDraft, setEditDraft] = useState({
    relation_type: "contextualizes" as Relationship["relation_type"],
    rationale: "",
    provenance_note: "",
  });
  const [mutationState, setMutationState] = useState<DataState>("idle");
  const activeWorkspaceRef = useRef("");
  const loadGenerationRef = useRef(0);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMobileMenuOpen(false);
      mobileMenuToggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileMenuOpen]);

  const selectedWorkspace = workspaces.find((workspace) => workspace.id === workspaceId) ?? null;
  const filteredRecords = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return records.filter((record) => {
      const matchesQuery = !needle ||
        [record.id, record.source_label ?? "", record.content, record.source_id ?? "", record.source_version_id ?? ""]
          .join(" ")
          .toLocaleLowerCase()
          .includes(needle);
      const hasLink = Boolean(record.source_label || (record.source_id && record.source_version_id));
      const matchesProvenance = provenanceFilter === "all" ||
        (provenanceFilter === "linked" ? hasLink : !hasLink);
      return matchesQuery && matchesProvenance;
    });
  }, [provenanceFilter, query, records]);
  const selectedRecord = filteredRecords.find((record) => record.id === selectedId)
    ?? filteredRecords[0]
    ?? null;

  const loadWorkspaceData = useCallback(async (id: string) => {
    const generation = ++loadGenerationRef.current;
    if (activeWorkspaceRef.current !== id) {
      activeWorkspaceRef.current = id;
      setRecords([]);
      setRelationships([]);
      setSelectedId("");
      setRelationshipDraft({ evidence_id_a: "", evidence_id_b: "", relation_type: "contextualizes", rationale: "", provenance_note: "" });
      setEditingRelationship("");
      setConfirmDeleteRelationshipId("");
    }
    if (!id) {
      setRecords([]);
      setRelationships([]);
      setRecordsState("idle");
      setRelationshipsState("idle");
      return;
    }
    setRecordsState("loading");
    setRelationshipsState("loading");
    setMessage("");
    const requests = [
      fetch(`/api/evidence/workspaces/${encodeURIComponent(id)}/evidence`, { cache: "no-store" }),
      fetch(`/api/evidence/workspaces/${encodeURIComponent(id)}/relationships`, { cache: "no-store" }),
    ] as const;
    const [evidenceResponse, relationshipsResponse] = await Promise.allSettled(requests);
    if (generation !== loadGenerationRef.current) return;

    if (evidenceResponse.status === "fulfilled" && evidenceResponse.value.ok) {
      const payload: unknown = await evidenceResponse.value.json();
      if (generation !== loadGenerationRef.current) return;
      if (Array.isArray(payload) && payload.every((item) =>
        isRecord(item) && item.workspace_id === id && typeof item.id === "string" && typeof item.content === "string"
      )) {
        const values = payload as Evidence[];
        setRecords(values);
        setSelectedId((current) => values.some((item) => item.id === current) ? current : values[0]?.id ?? "");
        setRecordsState("ready");
      } else {
        setRecords([]);
        setRecordsState("unavailable");
      }
    } else {
      setRecords([]);
      setRecordsState("unavailable");
      if (evidenceResponse.status === "fulfilled" && evidenceResponse.value.status === 401) {
        setWorkspaceState("unauthenticated");
      }
    }

    if (relationshipsResponse.status === "fulfilled" && relationshipsResponse.value.ok) {
      const payload: unknown = await relationshipsResponse.value.json();
      if (generation !== loadGenerationRef.current) return;
      if (Array.isArray(payload) && payload.every((item) =>
        isRecord(item) && item.workspace_id === id && typeof item.id === "string" &&
        typeof item.evidence_id_a === "string" && typeof item.evidence_id_b === "string" &&
        typeof item.rationale === "string" && typeof item.provenance_note === "string"
      )) {
        setRelationships(payload as Relationship[]);
        setRelationshipsState("ready");
      } else {
        setRelationships([]);
        setRelationshipsState("unavailable");
      }
    } else {
      setRelationships([]);
      setRelationshipsState("unavailable");
    }
  }, []);

  useEffect(() => {
    if (!account) return;
    if (new URLSearchParams(window.location.search).get("account") === "1") {
      setAccountOpen(true);
      setMobileMenuOpen(true);
      requestAnimationFrame(() => {
        document.getElementById("account-organization-title")?.scrollIntoView?.({ block: "start" });
      });
    }
  }, [account]);

  useEffect(() => {
    let active = true;
    async function loadMemberships() {
      setWorkspaces([]);
      setWorkspaceId("");
      setRecords([]);
      setRelationships([]);
      if (!account) {
        setWorkspaceState("unauthenticated");
        return;
      }
      setWorkspaceState("loading");
      try {
        const response = await fetch("/api/evidence/me", { cache: "no-store" });
        if (!active) return;
        if (response.status === 401) {
          setWorkspaceState("unauthenticated");
          return;
        }
        if (!response.ok) {
          setWorkspaceState("unavailable");
          return;
        }
        const payload: unknown = await response.json();
        if (!isRecord(payload) || payload.user_id !== account.id || !Array.isArray(payload.workspaces)) {
          setWorkspaceState("unavailable");
          return;
        }
        const memberships = payload.workspaces.filter((item): item is Workspace =>
          isRecord(item) && typeof item.id === "string" && typeof item.name === "string" &&
          (item.role === "owner" || item.role === "admin" || item.role === "member"),
        );
        setWorkspaces(memberships);
        setWorkspaceId((current) => memberships.some((item) => item.id === current) ? current : memberships[0]?.id ?? "");
        setWorkspaceState("ready");
      } catch {
        if (active) setWorkspaceState("unavailable");
      }
    }
    void loadMemberships();
    return () => { active = false; };
  }, [account]);

  useEffect(() => {
    void loadWorkspaceData(workspaceId);
  }, [loadWorkspaceData, workspaceId]);

  const createRelationship = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!workspaceId) return;
    const targetWorkspaceId = workspaceId;
    setMutationState("loading");
    try {
      const response = await fetch(`/api/evidence/workspaces/${targetWorkspaceId}/relationships`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(relationshipDraft),
      });
      if (!response.ok) {
        setMutationState("unavailable");
        setMessage(await responseError(response, "Relationship was not saved."));
        return;
      }
      if (workspaceId !== targetWorkspaceId) return;
      setRelationshipDraft({ evidence_id_a: "", evidence_id_b: "", relation_type: "contextualizes", rationale: "", provenance_note: "" });
      setMutationState("ready");
      await loadWorkspaceData(targetWorkspaceId);
    } catch {
      setMutationState("unavailable");
      setMessage("Relationship was not saved.");
    }
  };

  const updateRelationship = async (relationshipId: string) => {
    const targetWorkspaceId = workspaceId;
    setMutationState("loading");
    try {
      const response = await fetch(`/api/evidence/workspaces/${targetWorkspaceId}/relationships/${relationshipId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editDraft),
      });
      if (!response.ok) {
        setMutationState("unavailable");
        setMessage(await responseError(response, "Relationship was not updated."));
        return;
      }
      if (workspaceId !== targetWorkspaceId) return;
      setEditingRelationship("");
      setMutationState("ready");
      await loadWorkspaceData(targetWorkspaceId);
    } catch {
      setMutationState("unavailable");
      setMessage("Relationship was not updated.");
    }
  };

  const deleteRelationship = async (relationshipId: string) => {
    if (confirmDeleteRelationshipId !== relationshipId) {
      setEditingRelationship("");
      setConfirmDeleteRelationshipId(relationshipId);
      return;
    }
    setConfirmDeleteRelationshipId("");
    const targetWorkspaceId = workspaceId;
    setMutationState("loading");
    try {
      const response = await fetch(`/api/evidence/workspaces/${targetWorkspaceId}/relationships/${relationshipId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        setMutationState("unavailable");
        setMessage(await responseError(response, "Relationship was not deleted."));
        return;
      }
      if (workspaceId !== targetWorkspaceId) return;
      setMutationState("ready");
      await loadWorkspaceData(targetWorkspaceId);
    } catch {
      setMutationState("unavailable");
      setMessage("Relationship was not deleted.");
    }
  };

  const evidenceById = useMemo(() => new Map(records.map((record) => [record.id, record])), [records]);
  const isLoading = workspaceState === "loading";
  const sidebarItems = [
    { href: "/workspace", label: "Prepare evidence", status: selectedWorkspace ? "Workspace ready" : isLoading ? "Checking access" : "Unavailable" },
    { href: relationshipsOnly ? "/" : "#explorer", label: "Evidence", status: recordsState === "ready" ? `${records.length} records` : recordsState === "loading" ? "Loading" : "Unavailable" },
    { href: relationshipsOnly ? "#relationships" : "/relationships", label: "Relationships", status: relationshipsState === "ready" ? `${relationships.length} links` : relationshipsState === "loading" ? "Loading" : "Unavailable" },
    { href: "/workspace#agent-connection", label: "Connect an agent", status: "API / MCP" },
    { href: "/workspace#task-measurement", label: "Task outcomes", status: "Not connected" },
  ];

  return (
    <div className="live-shell">
      <a className="live-skip" href="#explorer">Skip to Evidence Explorer</a>
      <div className="live-mobile-bar">
        <Link className="live-brand" href="/">evidence<span>layer</span></Link>
        <button
          ref={mobileMenuToggleRef}
          type="button"
          className="live-menu-toggle"
          aria-expanded={mobileMenuOpen}
          aria-controls="live-sidebar"
          onClick={() => setMobileMenuOpen((value) => !value)}
        >
          {mobileMenuOpen ? "Close" : "Menu"}
        </button>
      </div>
      <aside id="live-sidebar" className={`live-sidebar${mobileMenuOpen ? " live-sidebar--open" : ""}`}>
        <Link className="live-brand live-brand--sidebar" href="/">evidence<span>layer</span></Link>
        <p className="live-sidebar-tagline">Evidence first. Conclusions remain inspectable.</p>
        <nav aria-label="Product navigation" className="live-nav">
          {sidebarItems.map((item) => item.href.startsWith("/") ? (
            <Link href={item.href} key={item.label} className="live-nav-item" onClick={() => setMobileMenuOpen(false)}>
              <span>{item.label}</span><small>{item.status}</small>
            </Link>
          ) : (
            <a href={item.href} key={item.label} className="live-nav-item" onClick={() => setMobileMenuOpen(false)}>
              <span>{item.label}</span><small>{item.status}</small>
            </a>
          ))}
        </nav>
        <div className="live-sidebar-footer">
          <Link href="/privacy">Privacy Policy</Link><Link href="/terms">Terms of Use</Link>
          <div className="live-controls">
            {account ? (
              <button type="button" className="live-account-button" onClick={() => setAccountOpen((value) => !value)} aria-expanded={accountOpen}>
                {accountOpen ? "Close account" : "Account"}
              </button>
            ) : <Link className="live-account-button" href="/login">Sign in</Link>}
            <ThemeToggle />
          </div>
          {account && accountOpen && <AuthAccount userId={account.id} email={account.email} />}
        </div>
      </aside>

      <main className="live-main">
        <header className="live-topbar">
          <div><span>Product</span><span aria-hidden="true"> / </span><strong>Evidence Explorer</strong></div>
          <span className={`live-status-pill ${workspaceState === "ready" ? "is-connected" : ""}`}>
            {workspaceState === "ready" ? "Authenticated workspace data" : workspaceState === "loading" ? "Checking access" : "Access unavailable"}
          </span>
        </header>
        <div className="live-content">
          <section className="live-intro" id="workspace">
            <div className="live-intro-copy">
              <p className="live-eyebrow">Examine · compare · verify</p>
              <h1>
                <span>Evidence</span>{" "}
                <span className="live-title-accent">
                  {relationshipsOnly ? "Relationships" : "Explorer"}
                </span>
              </h1>
              <p>{relationshipsOnly
                ? "Review and maintain the explicit evidence links that can help an agent understand how records relate."
                : "Prepare workspace evidence for an agent, inspect its source labels, and review the relationships recorded by workspace owners and admins."}</p>
              {!relationshipsOnly && (
                <p className="live-boundary-note">
                  Agents retrieve bounded evidence through API or MCP. This page does not run the agent
                  or report task-level token use, cost, or quality yet.
                </p>
              )}
            </div>
            {workspaceState === "ready" && workspaces.length > 0 && (
              <label className="live-workspace-picker">
                Workspace
                <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
                  {workspaces.map((workspace) => <option value={workspace.id} key={workspace.id}>{workspace.name} · {workspace.role}</option>)}
                </select>
              </label>
            )}
          </section>

          {workspaceState === "unauthenticated" && (
            <section className="live-notice" role="status"><h2>Sign in to inspect evidence</h2><p>Your workspace records are not shown until the server verifies your session.</p><Link href="/login">Sign in</Link></section>
          )}
          {workspaceState === "unavailable" && (
            <section className="live-notice live-notice--error" role="alert"><h2>Workspace access is unavailable</h2><p>Membership could not be verified. No workspace data is displayed.</p><Link href="/workspace">Open Workspace access</Link></section>
          )}
          {workspaceState === "ready" && workspaces.length === 0 && (
            <section className="live-notice"><h2>No active workspace membership</h2><p>Create or join a workspace before adding or inspecting evidence.</p><Link href="/workspace">Set up Workspace</Link></section>
          )}
          {message && <p className="live-error" role="alert">{message}</p>}

          {selectedWorkspace && (
            <>
              {!relationshipsOnly && (
              <section id="explorer" className="live-section">
                <div className="live-section-heading"><div><p className="live-eyebrow">{selectedWorkspace.name} · authorized records</p><h2>Evidence</h2></div><span>{records.length} records</span></div>
                <div className="live-filters">
                  <label className="live-search">Search actual evidence records
                    <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search evidence text, source, or ID" />
                  </label>
                  <label className="live-search">Filter by source provenance
                    <select value={provenanceFilter} onChange={(event) => setProvenanceFilter(event.target.value as typeof provenanceFilter)}>
                      <option value="all">All records</option>
                      <option value="linked">Has a source reference</option>
                      <option value="unlinked">No source reference</option>
                    </select>
                  </label>
                </div>
                {recordsState === "loading" && <p className="live-muted" role="status">Loading workspace evidence…</p>}
                {recordsState === "unavailable" && <p className="live-error" role="alert">Evidence service is unavailable. The Explorer fails closed and does not substitute sample records.</p>}
                {recordsState === "ready" && records.length === 0 && (
                  <div className="live-empty"><h3>No evidence records yet</h3><p>Add an evidence record in Workspace, then link it to a published source version when available.</p><Link href="/workspace">Open Workspace</Link></div>
                )}
                {recordsState === "ready" && records.length > 0 && (
                  <div className="live-evidence-grid">
                    <div className="live-record-list" aria-label="Evidence records">
                      {filteredRecords.length === 0 ? <p className="live-empty">No records match this search.</p> : filteredRecords.map((record) => (
                        <button className={`live-record${selectedRecord?.id === record.id ? " is-selected" : ""}`} type="button" key={record.id} onClick={() => setSelectedId(record.id)} aria-pressed={selectedRecord?.id === record.id}>
                          <span>{record.source_label || "Evidence record"}</span><strong>{record.content.slice(0, 170)}{record.content.length > 170 ? "…" : ""}</strong><small>{record.id}</small>
                        </button>
                      ))}
                    </div>
                    <article className="live-record-detail">
                      {selectedRecord ? (
                        <>
                          <p className="live-eyebrow">Original evidence record · {selectedRecord.id}</p>
                          <h3>{selectedRecord.source_label || "Untitled evidence"}</h3>
                          <blockquote>{selectedRecord.content}</blockquote>
                          <dl className="live-provenance">
                            <div><dt>Source label</dt><dd>{selectedRecord.source_label || "No source label"}</dd></div>
                            <div><dt>Verified snapshot</dt><dd>{selectedRecord.source_id && selectedRecord.source_version_id
                              ? `Source ${selectedRecord.source_id} · version ${selectedRecord.source_version_id}`
                              : "No exact verified snapshot linked"}</dd></div>
                            <div><dt>Created</dt><dd>{new Date(selectedRecord.created_at).toLocaleString()}</dd></div>
                          </dl>
                          <p className="live-boundary-note">
                            A connector source label (such as a GitHub file path) is different from
                            an exact, verified snapshot link. Open Workspace to inspect authorized
                            source versions.
                          </p>
                          <Link href="/workspace#source-read-heading">Inspect verified snapshots</Link>
                        </>
                      ) : <p>Select a record to inspect its text and provenance.</p>}
                    </article>
                  </div>
                )}
              </section>
              )}

              <section id="relationships" className="live-section">
                <div className="live-section-heading"><div><p className="live-eyebrow">Explicit, persisted links only</p><h2>Relationships</h2></div><span>{relationships.length} links</span></div>
                {relationshipsState === "loading" && <p className="live-muted" role="status">Loading recorded relationships…</p>}
                {relationshipsState === "unavailable" && <p className="live-error" role="alert">Relationship service is unavailable. No fixture-defined or inferred links are shown.</p>}
                {relationshipsState === "ready" && relationships.length === 0 && <div className="live-empty"><h3>No relationships recorded</h3><p>Only workspace owners and admins can document a relationship. Similarity alone is never treated as proof.</p></div>}
                {relationshipsState === "ready" && relationships.length > 0 && (
                  <RelationshipCanvas
                    records={records}
                    relationships={relationships}
                    onOpenEvidence={(id) => setSelectedId(id)}
                  />
                )}
                {relationshipsState === "ready" && relationships.map((relationship) => {
                  const recordA = evidenceById.get(relationship.evidence_id_a);
                  const recordB = evidenceById.get(relationship.evidence_id_b);
                  return (
                    <article className="live-relationship" key={relationship.id}>
                      <div className="live-relationship-pair">
                        <button type="button" onClick={() => recordA && setSelectedId(recordA.id)}>{recordA?.source_label || relationship.evidence_id_a}</button>
                        <span aria-label={RELATION_LABELS[relationship.relation_type]}>{RELATION_LABELS[relationship.relation_type]}</span>
                        <button type="button" onClick={() => recordB && setSelectedId(recordB.id)}>{recordB?.source_label || relationship.evidence_id_b}</button>
                      </div>
                      <p>{relationship.rationale}</p>
                      <small>Basis: {relationship.provenance_note} · recorded by {relationship.created_by} · updated {new Date(relationship.updated_at).toLocaleString()}</small>
                      {selectedWorkspace.role !== "member" && editingRelationship === relationship.id ? (
                        <div className="live-edit-form">
                          <select aria-label="Relationship type" value={editDraft.relation_type} onChange={(event) => setEditDraft({ ...editDraft, relation_type: event.target.value as Relationship["relation_type"] })}>
                            {Object.entries(RELATION_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
                          </select>
                          <textarea aria-label="Relationship rationale" value={editDraft.rationale} onChange={(event) => setEditDraft({ ...editDraft, rationale: event.target.value })} />
                          <textarea aria-label="Relationship basis" value={editDraft.provenance_note} onChange={(event) => setEditDraft({ ...editDraft, provenance_note: event.target.value })} />
                          <button type="button" onClick={() => void updateRelationship(relationship.id)}>Save</button>
                          <button type="button" onClick={() => setEditingRelationship("")}>Cancel</button>
                        </div>
                      ) : selectedWorkspace.role !== "member" ? (
                        <div className="live-row-actions">
                          <button type="button" onClick={() => {
                            setEditingRelationship(relationship.id);
                            setConfirmDeleteRelationshipId("");
                            setEditDraft({ relation_type: relationship.relation_type, rationale: relationship.rationale, provenance_note: relationship.provenance_note });
                          }}>Edit</button>
                          {confirmDeleteRelationshipId === relationship.id ? (
                            <>
                              <span role="alert">Delete this relationship? This cannot be undone.</span>
                              <button type="button" onClick={() => void deleteRelationship(relationship.id)}>Confirm delete</button>
                              <button type="button" onClick={() => setConfirmDeleteRelationshipId("")}>Cancel</button>
                            </>
                          ) : (
                            <button type="button" onClick={() => void deleteRelationship(relationship.id)}>Delete</button>
                          )}
                        </div>
                      ) : null}
                    </article>
                  );
                })}
                {relationshipsState === "ready" && selectedWorkspace.role !== "member" && records.length > 1 && (
                  <form className="live-relationship-form" onSubmit={(event) => void createRelationship(event)}>
                    <h3>Document a relationship</h3>
                    <p>Record an explicit, reviewable link. This does not claim causation or infer a relationship from text.</p>
                    <div className="live-form-pair">
                      <label>Evidence A<select required value={relationshipDraft.evidence_id_a} onChange={(event) => setRelationshipDraft({ ...relationshipDraft, evidence_id_a: event.target.value })}><option value="">Choose evidence</option>{records.map((record) => <option value={record.id} key={record.id}>{record.source_label || record.id}</option>)}</select></label>
                      <label>Evidence B<select required value={relationshipDraft.evidence_id_b} onChange={(event) => setRelationshipDraft({ ...relationshipDraft, evidence_id_b: event.target.value })}><option value="">Choose evidence</option>{records.map((record) => <option value={record.id} key={record.id}>{record.source_label || record.id}</option>)}</select></label>
                    </div>
                    <label>Relationship type<select value={relationshipDraft.relation_type} onChange={(event) => setRelationshipDraft({ ...relationshipDraft, relation_type: event.target.value as Relationship["relation_type"] })}>{Object.entries(RELATION_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                    <label>Rationale<textarea required maxLength={4000} value={relationshipDraft.rationale} onChange={(event) => setRelationshipDraft({ ...relationshipDraft, rationale: event.target.value })} /></label>
                    <label>Provenance / review basis<textarea required maxLength={2000} value={relationshipDraft.provenance_note} onChange={(event) => setRelationshipDraft({ ...relationshipDraft, provenance_note: event.target.value })} /></label>
                    <button type="submit" disabled={mutationState === "loading"}>Save relationship</button>
                  </form>
                )}
              </section>

              <footer className="live-footer"><span>Evidence Layer · Focused context for agent tasks.</span><nav><Link href="/workspace#agent-connection">Connect an agent</Link><Link href="/documentation">Documentation</Link><Link href="/evaluation">Historical benchmarks</Link><Link href="/cost-performance">Cost estimates</Link><Link href="/privacy">Privacy</Link><Link href="/terms">Terms</Link></nav></footer>
            </>
          )}
        </div>
      </main>

    </div>
  );
}
