import Link from "next/link";
import type { ReactNode } from "react";
import type { WorkspaceDashboard, WorkspaceDashboardResult } from "./workspace-data";

export default function WorkspaceOverview({
  result,
  email,
}: {
  result: WorkspaceDashboardResult;
  email: string;
}) {
  if (result.kind === "no-workspace") {
    return (
      <WorkspaceFrame>
        <section className="workspace-basic-card" role="status">
          <p className="eyebrow">Workspace</p>
          <h1>No active workspace</h1>
          <p>This account does not have an active workspace membership.</p>
        </section>
      </WorkspaceFrame>
    );
  }

  if (result.kind === "unavailable") {
    return (
      <WorkspaceFrame>
        <section className="workspace-basic-card workspace-basic-card--error" role="alert">
          <p className="eyebrow">Workspace</p>
          <h1>Workspace data is unavailable</h1>
          <p>Access could not be verified. No workspace or evidence data is shown.</p>
        </section>
      </WorkspaceFrame>
    );
  }

  return (
    <WorkspaceFrame>
      <div className="workspace-basic-heading">
        <div>
          <p className="eyebrow">Evidence Layer · Workspace</p>
          <h1>Workspace</h1>
        </div>
        <Link className="workspace-basic-explorer" href="/">
          Open Evidence Explorer
        </Link>
      </div>
      <div className="workspace-basic-grid">
        <WorkspaceSummary dashboard={result.dashboard} />
        <section className="workspace-basic-card" aria-labelledby="workspace-evidence-heading">
          <p className="eyebrow">Evidence Records</p>
          <h2 id="workspace-evidence-heading">
            {result.dashboard.evidence.kind === "ready"
              ? result.dashboard.evidence.count
              : "Unavailable"}
          </h2>
          <EvidenceList evidence={result.dashboard.evidence} />
        </section>
        <section className="workspace-basic-card" aria-labelledby="workspace-access-heading">
          <p className="eyebrow">Access</p>
          <h2 id="workspace-access-heading">Current account</h2>
          <dl className="workspace-basic-details">
            <div>
              <dt>User</dt>
              <dd>{email || "Signed-in account"}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{roleLabel(result.dashboard.role)}</dd>
            </div>
          </dl>
        </section>
      </div>
    </WorkspaceFrame>
  );
}

function WorkspaceSummary({ dashboard }: { dashboard: WorkspaceDashboard }) {
  return (
    <section className="workspace-basic-card" aria-labelledby="workspace-overview-heading">
      <p className="eyebrow">Workspace Overview</p>
      <h2 id="workspace-overview-heading">{dashboard.workspace.name}</h2>
      <dl className="workspace-basic-details">
        <div>
          <dt>Name</dt>
          <dd>{dashboard.workspace.name}</dd>
        </div>
        <div>
          <dt>Type</dt>
          <dd>Not recorded in the current schema</dd>
        </div>
      </dl>
    </section>
  );
}

function EvidenceList({ evidence }: { evidence: WorkspaceDashboard["evidence"] }) {
  if (evidence.kind === "unavailable") {
    return (
      <p className="workspace-basic-message" role="alert">
        Evidence records could not be loaded. Reload the page to try again.
      </p>
    );
  }
  if (evidence.count === 0) {
    return (
      <p className="workspace-basic-message">
        No evidence in this workspace yet.
      </p>
    );
  }
  return (
    <>
      <ul className="workspace-basic-records">
        {evidence.records.map((record) => (
          <li key={record.id}>
            <strong>{record.source_label || "Evidence record"}</strong>
            <p>{record.content}</p>
          </li>
        ))}
      </ul>
      {evidence.count > evidence.records.length && (
        <p className="workspace-basic-message">
          Showing the {evidence.records.length} most recent records.
        </p>
      )}
    </>
  );
}

function roleLabel(role: WorkspaceDashboard["role"]): string {
  return role === "owner" ? "Owner" : "Member";
}

function WorkspaceFrame({ children }: { children: ReactNode }) {
  return (
    <main className="workspace-basic">
      <header className="workspace-basic-header">
        <Link className="workspace-basic-brand" href="/">
          evidence<span>layer</span>
        </Link>
        <Link href="/">Evidence Explorer</Link>
      </header>
      <div className="workspace-basic-content">{children}</div>
    </main>
  );
}
