import { redirect } from "next/navigation";
import EvidenceExplorer from "./evidence-explorer";
import { getVerifiedAuthContext } from "../lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") redirect("/login?reason=sign-in");
  if (auth.kind === "unavailable") redirect("/login?reason=session-check");
  return <EvidenceExplorer />;
}
