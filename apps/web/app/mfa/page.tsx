import PageTitle from "../../components/page-title";
import SiteFooter from "../../components/site-footer";
import SiteHeader from "../../components/site-header";
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
        <SiteHeader />
        <section aria-labelledby="mfa-gate-title" className="workspace-gate">
          <p className="eyebrow">Evidence Layer · Security</p>
          <h1 id="mfa-gate-title"><PageTitle>Authentication service is unavailable</PageTitle></h1>
          <p>Two-factor authentication could not be verified. No workspace data was loaded.</p>
        </section>
        <SiteFooter />
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
      <SiteHeader accountEmail={auth.user.email ?? null} />
      <MfaPanel next={next} />
      <SiteFooter />
    </main>
  );
}
