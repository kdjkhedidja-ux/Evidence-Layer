import { redirect } from "next/navigation";
import EvidenceExplorer from "./live-evidence-explorer";
import { getMfaChallengeStatus } from "../lib/supabase/mfa";
import { getVerifiedAuthContext } from "../lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "authenticated") {
    const mfaStatus = await getMfaChallengeStatus(auth.client);
    if (mfaStatus === "unavailable") redirect("/auth/error?reason=mfa-unavailable");
    if (mfaStatus === "required") redirect("/mfa?next=%2F");
  }
  return <EvidenceExplorer account={auth.kind === "authenticated"
    ? { id: auth.user.id, email: auth.user.email ?? "Account" }
    : null} />;
}
