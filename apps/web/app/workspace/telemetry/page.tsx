import { redirect } from "next/navigation";

import PageTitle from "../../../components/page-title";
import SiteHeader from "../../../components/site-header";
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
      <SiteHeader accountEmail={auth.user.email ?? null} />
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
      <SiteHeader />
      <section className="workspace-gate">
        <p className="eyebrow">Evidence Layer · Telemetry</p>
        <h1><PageTitle>{title}</PageTitle></h1>
        <p>Telemetry was not loaded. Try again later.</p>
      </section>
    </main>
  );
}
