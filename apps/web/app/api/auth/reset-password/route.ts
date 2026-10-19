import { cookies } from "next/headers";
import { getVerifiedAuthContext } from "../../../../lib/supabase/server";
import { hasRecoveryProof, RECOVERY_COOKIE } from "../../../../lib/supabase/recovery";

export const dynamic = "force-dynamic";

function result(status: number, message: string, code?: string): Response {
  return Response.json({ message, ...(code ? { code } : {}) }, {
    status, headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request): Promise<Response> {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    return result(403, "Request origin was not accepted.");
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind !== "authenticated") return result(401, "The recovery link is invalid or expired.");
  const cookieStore = await cookies();
  if (!hasRecoveryProof(cookieStore.get(RECOVERY_COOKIE)?.value, auth.accessToken)) {
    return result(403, "The recovery link is invalid or expired.");
  }

  let password: unknown;
  try {
    const body = await request.json();
    password = body?.password;
  } catch {
    return result(400, "Enter a valid new password.");
  }
  if (typeof password !== "string" || password.length < 12 || password.length > 1024) {
    return result(400, "Use a password of at least 12 characters.");
  }

  try {
    const { error } = await auth.client.auth.updateUser({ password });
    if (error) return result(400, "We could not update the password. Request a new link and try again.");
  } catch {
    return result(503, "The authentication service could not be reached. Try again later.");
  }

  try {
    cookieStore.delete(RECOVERY_COOKIE);
    const { error: signOutError } = await auth.client.auth.signOut({ scope: "local" });
    if (signOutError) return result(503, "Password changed, but sign out could not be confirmed.", "signout-unconfirmed");
    return result(200, "Password updated. Sign in with your new password.");
  } catch {
    return result(503, "Password changed, but sign out could not be confirmed.", "signout-unconfirmed");
  }
}
