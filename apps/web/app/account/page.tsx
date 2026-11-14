import { redirect } from "next/navigation";

import AuthAccount from "../../components/auth-account";
import PageTitle from "../../components/page-title";
import SiteHeader from "../../components/site-header";
import { getMfaChallengeStatus } from "../../lib/supabase/mfa";
import { getVerifiedAuthContext } from "../../lib/supabase/server";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Account · Evidence Layer",
  description: "Manage your Evidence Layer account, workspaces, exports, deletion, and session.",
};

export default async function AccountPage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unavailable") {
    return <AccountGate title="Account service is unavailable" />;
  }
  if (auth.kind === "unauthenticated") {
    redirect("/login?next=%2Faccount");
  }
  const mfaStatus = await getMfaChallengeStatus(auth.client);
  if (mfaStatus === "unavailable") {
    return <AccountGate title="Security check is unavailable" />;
  }
  if (mfaStatus === "required") {
    redirect("/mfa?next=%2Faccount");
  }

  return (
    <main className="workspace-page">
      <SiteHeader accountEmail={auth.user.email ?? null} />
      <div className="workspace-main">
        <div className="workspace-title-row">
          <div>
            <p className="eyebrow">Workspace · account</p>
            <h1><PageTitle>Account</PageTitle></h1>
            <p className="workspace-lede">
              Manage your account, workspace access, data export, deletion requests, and session.
            </p>
          </div>
        </div>
        <AuthAccount userId={auth.user.id} email={auth.user.email ?? "Account"} />
      </div>
    </main>
  );
}

function AccountGate({ title }: { title: string }) {
  return (
    <main className="workspace-page workspace-page--gate">
      <SiteHeader />
      <section className="workspace-gate">
        <p className="eyebrow">Evidence Layer · Account</p>
        <h1><PageTitle>{title}</PageTitle></h1>
        <p>The account page could not be loaded. Try again later.</p>
      </section>
    </main>
  );
}
