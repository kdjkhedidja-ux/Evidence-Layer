import EvidenceExplorer from "./live-evidence-explorer";
import { getVerifiedAuthContext } from "../lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const auth = await getVerifiedAuthContext();
  return <EvidenceExplorer account={auth.kind === "authenticated"
    ? { id: auth.user.id, email: auth.user.email ?? "Account" }
    : null} />;
}
