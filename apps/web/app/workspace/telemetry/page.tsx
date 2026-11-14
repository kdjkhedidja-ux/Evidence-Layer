import Link from "next/link";
import { redirect } from "next/navigation";

import PageTitle from "../../../components/page-title";
import TelemetryDashboard from "../../../components/telemetry-dashboard";
import { getMfaChallengeStatus } from "../../../lib/supabase/mfa";
import { getVerifiedAuthContext } from "../../../lib/supabase/server";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Telemetry · Evidence Layer",
  description: "Browser-local retrieval telemetry for task-context preparation.",
};

export default async function WorkspaceTelemetryPage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unavailable") {
    return <TelemetryGate title="Workspace service is unavailable" />;
  }
  if (auth.kind === "unauthenticated") {
    redirect("/login?next=%2Fworkspace%2Ftelemetry");
  }
  const mfaStatus = await getMfaChallengeStatus(auth.client);
  if (mfaStatus === "unavailable") {
    return <TelemetryGate title="Security check is unavailable" />;
  }
  if (mfaStatus === "required") redirect("/mfa?next=%2Fworkspace%2Ftelemetry");

  return (
    <main className="workspace-page">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <nav aria-label="Workspace navigation">
          <Link href="/workspace">Workspace</Link>
          <Link aria-current="page" href="/workspace/telemetry">Telemetry</Link>
          <Link href="/">Evidence Explorer</Link>
          <Link href="/relationships">Relationships</Link>
          <Link href="/documentation">Documentation</Link>
        </nav>
        <div className="workspace-user">
          <span>{auth.user.email ?? "Account"}</span>
          <Link href="/workspace">Back to workspace</Link>
        </div>
      </header>
      <section className="workspace-intro">
        <p className="eyebrow">Workspace · telemetry</p>
        <h1><PageTitle>Telemetry</PageTitle></h1>
        <p>
          Review the retrieval side of task-context preparation. The current view is local to this
          browser and does not yet include agent execution, provider cost, or answer quality.
        </p>
      </section>
      <TelemetryDashboard />
    </main>
  );
}

function TelemetryGate({ title }: { title: string }) {
  return (
    <main className="workspace-page workspace-page--gate">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <Link href="/workspace">Back to workspace</Link>
      </header>
      <section className="workspace-gate">
        <p className="eyebrow">Evidence Layer · Telemetry</p>
        <h1><PageTitle>{title}</PageTitle></h1>
        <p>Telemetry was not loaded. Try again later.</p>
      </section>
    </main>
  );
}
