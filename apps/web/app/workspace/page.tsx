import Link from "next/link";
import type { ReactNode } from "react";
import { getVerifiedAuthContext } from "../../lib/supabase/server";
import WorkspaceOverview from "./workspace-overview";
import { loadWorkspaceDashboard } from "./workspace-data";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Workspace | Evidence Layer",
  description: "View your workspace, evidence records, and current access role.",
};

export default async function WorkspacePage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unavailable") {
    return (
      <WorkspaceGate
        title="Workspace is unavailable"
        description="Identity verification could not be reached. No workspace data is shown."
      />
    );
  }
  if (auth.kind === "unauthenticated") {
    return (
      <WorkspaceGate
        title="Sign in to open Workspace"
        description="Workspace details are available only after the server verifies your account."
        action={<Link href="/login">Sign in</Link>}
      />
    );
  }

  const result = await loadWorkspaceDashboard(auth.client, auth.user.id);
  return <WorkspaceOverview result={result} email={auth.user.email ?? ""} />;
}

function WorkspaceGate({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <main className="workspace-basic">
      <header className="workspace-basic-header">
        <Link className="workspace-basic-brand" href="/">
          evidence<span>layer</span>
        </Link>
        <Link href="/">Evidence Explorer</Link>
      </header>
      <section className="workspace-basic-gate" role={action ? "status" : "alert"}>
        <p className="eyebrow">Evidence Layer · Workspace</p>
        <h1>{title}</h1>
        <p>{description}</p>
        {action}
      </section>
    </main>
  );
}
