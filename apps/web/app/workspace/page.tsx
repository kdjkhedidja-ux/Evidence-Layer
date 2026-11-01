import Link from "next/link";
import type { ReactNode } from "react";
import WorkspaceConsole from "./workspace-console";
import { getVerifiedAuthContext } from "../../lib/supabase/server";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Workspace · Evidence Layer",
  description: "View authorized Evidence Layer workspaces and evidence records.",
};

export default async function WorkspacePage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unavailable") {
    return (
      <WorkspaceGate
        title="Workspace service is unavailable"
        description="Identity configuration or verification could not be reached. No workspace data was loaded."
      />
    );
  }
  if (auth.kind === "unauthenticated") {
    return (
      <WorkspaceGate
        title="Sign in to open your workspace"
        description="Workspace records are available only after the server verifies your account."
        action={<Link href="/login">Sign in</Link>}
      />
    );
  }
  return (
    <WorkspaceConsole
      userId={auth.user.id}
      email={auth.user.email ?? "Account"}
    />
  );
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
    <main className="workspace-page workspace-page--gate">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <Link href="/">Demo / Synthetic</Link>
      </header>
      <section aria-labelledby="workspace-gate-title" className="workspace-gate">
        <p className="eyebrow">Evidence Layer · Workspace</p>
        <h1 id="workspace-gate-title">{title}</h1>
        <p>{description}</p>
        {action}
      </section>
    </main>
  );
}
