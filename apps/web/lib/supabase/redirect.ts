function containsUnsafeRedirectCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return character === "\\" || code < 0x20 || code === 0x7f;
  });
}

export function safeInternalPath(value: string | null | undefined, fallback = "/"): string {
  if (!value?.startsWith("/") || value.startsWith("//")) return fallback;
  if (containsUnsafeRedirectCharacter(value) || /%5c/i.test(value)) return fallback;

  try {
    const parsed = new URL(value, "https://evidence-layer.invalid");
    if (parsed.origin !== "https://evidence-layer.invalid") return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function authCallbackUrl(origin: string, next: string): string {
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", next);
  return callback.toString();
}

export function callbackFailureReason(
  error: string | null,
  errorCode: string | null,
): "expired-link" | "invalid-link" {
  if (errorCode === "otp_expired" || errorCode === "access_token_expired") {
    return "expired-link";
  }
  if (error === "access_denied" && errorCode === "otp_expired") return "expired-link";
  return "invalid-link";
}
