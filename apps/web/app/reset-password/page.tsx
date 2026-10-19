import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import AuthForm from "../../components/auth-form";
import { getVerifiedAuthContext } from "../../lib/supabase/server";
import { hasRecoveryProof, RECOVERY_COOKIE } from "../../lib/supabase/recovery";

export const dynamic = "force-dynamic";

export default async function ResetPasswordPage() {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") redirect("/auth/error?reason=expired-link");
  if (auth.kind === "unavailable") redirect("/auth/error?reason=configuration");
  const recoveryCookie = (await cookies()).get(RECOVERY_COOKIE)?.value;
  if (!hasRecoveryProof(recoveryCookie, auth.accessToken)) {
    redirect("/auth/error?reason=invalid-link");
  }
  return <AuthForm mode="reset-password" />;
}
