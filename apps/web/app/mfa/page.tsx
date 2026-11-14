import Link from "next/link";
import PageTitle from "../../components/page-title";
import { redirect } from "next/navigation";
import { getVerifiedAuthContext } from "../../lib/supabase/server";
import { safeInternalPath } from "../../lib/supabase/redirect";
import MfaPanel from "./mfa-panel";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Two-factor authentication · Evidence Layer",
  description: "Verify or enroll a time-based one-time password for this account.",
};

export default async function MfaPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unavailable") {
    return (
      <main className="workspace-page workspace-page--gate">
        <header className="workspace-header">
          <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
          <Link href="/">Open Explorer</Link>
        </header>
        <section aria-labelledby="mfa-gate-title" className="workspace-gate">
          <p className="eyebrow">Evidence Layer · Security</p>
          <h1 id="mfa-gate-title"><PageTitle>Authentication service is unavailable</PageTitle></h1>
          <p>Two-factor authentication could not be verified. No workspace data was loaded.</p>
        </section>
      </main>
    );
  }
  if (auth.kind === "unauthenticated") {
    redirect("/login?next=%2Fmfa");
  }

  const params = await searchParams;
  const next = safeInternalPath(params.next, "/workspace");

  return (
    <main className="workspace-page">
      <header className="workspace-header">
        <Link className="workspace-brand" href="/">evidence<span>layer</span></Link>
        <nav aria-label="Account">
          <Link href="/workspace">Workspace</Link>
          <Link href="/">Explorer</Link>
        </nav>
        <div className="workspace-user">
          <span>{auth.user.email ?? "Account"}</span>
        </div>
      </header>
      <MfaPanel next={next} />
    </main>
  );
}
