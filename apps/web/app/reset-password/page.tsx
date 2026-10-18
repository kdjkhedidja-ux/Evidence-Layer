import { redirect } from "next/navigation";
import AuthForm from "../../components/auth-form";
import { getVerifiedAuthContext } from "../../lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function ResetPasswordPage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") redirect("/auth/error?reason=expired-link");
  if (auth.kind === "unavailable") redirect("/auth/error?reason=configuration");
  return <AuthForm mode="reset-password" />;
}
