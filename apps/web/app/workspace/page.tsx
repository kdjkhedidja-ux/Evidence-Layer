import Link from "next/link";
import PageTitle from "../../components/page-title";
import SiteHeader from "../../components/site-header";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import WorkspaceConsole from "./workspace-console";
import { getMfaChallengeStatus } from "../../lib/supabase/mfa";
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
  if (auth.kind === "authenticated") {
    const mfaStatus = await getMfaChallengeStatus(auth.client);
    if (mfaStatus === "unavailable") {
      return (
        <WorkspaceGate
          title="Security check is unavailable"
          description="Two-factor authentication could not be verified. No workspace data was loaded. Try again later."
        />
      );
    }
    if (mfaStatus === "required") redirect("/mfa?next=%2Fworkspace");
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
      <SiteHeader />
      <section aria-labelledby="workspace-gate-title" className="workspace-gate">
        <p className="eyebrow">Evidence Layer · Workspace</p>
        <h1 id="workspace-gate-title"><PageTitle>{title}</PageTitle></h1>
        <p>{description}</p>
        {action}
      </section>
    </main>
  );
}
