export type SupabasePublicConfig = {
  url: string;
  publishableKey: string;
};

export class SupabaseConfigurationError extends Error {
  constructor() {
    super("Supabase public configuration is unavailable");
    this.name = "SupabaseConfigurationError";
  }
}

export function getSupabasePublicConfig(): SupabasePublicConfig | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim();
  if (!url || !publishableKey?.startsWith("sb_publishable_")) {
    return null;
  }

  try {
    const parsed = new URL(url);
    const secure = parsed.protocol === "https:";
    const local =
      parsed.protocol === "http:" &&
      (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");
    if (
      (!secure && !local) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash ||
      !["", "/"].includes(parsed.pathname)
    ) {
      return null;
    }
  } catch {
    return null;
  }

  return { url: url.replace(/\/$/, ""), publishableKey };
}

export function getEvidenceServiceBaseUrl(): string | null {
  const value = process.env.EVIDENCE_SERVICE_URL?.trim();
  if (!value) return null;

  try {
    const parsed = new URL(value);
    const secure = parsed.protocol === "https:";
    const local =
      parsed.protocol === "http:" &&
      (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");
    if (
      (!secure && !local) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash ||
      !["", "/"].includes(parsed.pathname)
    ) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}
