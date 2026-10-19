import AuthForm from "../../components/auth-form";
import { safeInternalPath } from "../../lib/supabase/redirect";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function loginNotice(reason: string | undefined): string | undefined {
  switch (reason) {
    case "signed-out": return "You have been signed out.";
    case "session-expired": return "Your session expired. Sign in again to continue.";
    case "session-check": return "We could not verify your session. Try signing in again.";
    case "configuration": return "Authentication needs to be configured for this environment.";
    default: return undefined;
  }
}

export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const next = typeof params.next === "string" ? params.next : "/";
  const reason = typeof params.reason === "string" ? params.reason : undefined;
  return <AuthForm mode="login" returnTo={safeInternalPath(next)} notice={loginNotice(reason)} />;
}
