const CALLBACK_PATH = "/api/connectors/notion/callback";

/**
 * NOTION_REDIRECT_URI must be the app callback, not Notion's authorization URL.
 * Reject a misconfigured value rather than sending users back to Notion.
 */
export function resolveNotionRedirectUri({
  configured,
  requestUrl,
}: {
  configured?: string;
  requestUrl: string;
}): string | null {
  const fallback = new URL(CALLBACK_PATH, requestUrl).toString();
  if (!configured) return fallback;
  let parsed: URL;
  try {
    parsed = new URL(configured);
  } catch {
    return null;
  }
  const local = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) return null;
  if (parsed.hostname === "api.notion.com") return null;
  if (!parsed.pathname.endsWith(CALLBACK_PATH)) return null;
  return parsed.toString();
}
