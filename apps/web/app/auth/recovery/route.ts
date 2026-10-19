import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function invalidLink(request: Request): NextResponse {
  const response = NextResponse.redirect(new URL("/auth/error?reason=invalid-link", request.url));
  response.headers.set("Cache-Control", "no-store");
  return response;
}

function escapeAttribute(value: string): string {
  const entities: Record<string, string> = {
    "&": "&amp;",
    '"': "&quot;",
    "'": "&#39;",
    "<": "&lt;",
    ">": "&gt;",
  };
  return value.replace(/[&"'<>]/g, (character) => entities[character] ?? character);
}

export function GET(request: Request): Response {
  const url = new URL(request.url);
  const tokenHashes = url.searchParams.getAll("token_hash");
  const tokenHash = tokenHashes[0];
  if (
    tokenHashes.length !== 1 ||
    !tokenHash ||
    tokenHash.length > 512 ||
    !/^[A-Za-z0-9._~+/=-]+$/.test(tokenHash)
  ) {
    return invalidLink(request);
  }

  const body = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reset your password · Evidence Layer</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; background: #0d141a; color: #ecf1f2; }
    main { box-sizing: border-box; width: min(100%, 28rem); padding: 2.5rem 1.5rem; }
    .brand { color: #ecf1f2; font-size: 1.3rem; font-weight: 700; text-decoration: none; }
    .brand span { color: #80d8bb; }
    .eyebrow { margin-top: 2.5rem; color: #80d8bb; font-size: .75rem; font-weight: 700; letter-spacing: .14em; }
    h1 { margin: .65rem 0; font-size: 2rem; line-height: 1.2; }
    p { color: #b7c5ca; line-height: 1.6; }
    button { width: 100%; margin-top: 1.25rem; padding: .9rem 1rem; border: 0; border-radius: .4rem; background: #80d8bb; color: #0d141a; font: inherit; font-weight: 700; cursor: pointer; }
    button:focus-visible, a:focus-visible { outline: 3px solid #ecf1f2; outline-offset: 3px; }
  </style>
</head>
<body>
  <main>
    <a class="brand" href="/login">evidence<span>layer</span></a>
    <p class="eyebrow">ACCOUNT RECOVERY</p>
    <h1>Continue to reset your password</h1>
    <p>Press the button to verify this one-time link and choose a new password.</p>
    <form method="get" action="/auth/callback">
      <input type="hidden" name="token_hash" value="${escapeAttribute(tokenHash)}">
      <input type="hidden" name="type" value="recovery">
      <button type="submit">Continue</button>
    </form>
  </main>
</body>
</html>`;

  return new Response(body, {
    status: 200,
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}
