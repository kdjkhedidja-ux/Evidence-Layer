// Both mock servers import this module in separate processes. Playwright
// passes one shared issue time so the two processes mint identical tokens even
// when they start in different seconds (otherwise the evidence mock rejects the
// session that the auth mock issued, which made the e2e suite flaky).
const sharedIssuedAt = Number.parseInt(process.env.SYNTHETIC_TOKEN_ISSUED_AT ?? "", 10);
const issuedAt = Number.isSafeInteger(sharedIssuedAt) && sharedIssuedAt > 0
  ? sharedIssuedAt
  : Math.floor(Date.now() / 1000);

function token(subject, method) {
  const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({
    sub: subject,
    aud: "authenticated",
    role: "authenticated",
    aal: "aal1",
    amr: [{ method, timestamp: issuedAt }],
    exp: issuedAt + 3600,
    iat: issuedAt,
    session_id: `synthetic-session-${subject}`,
  })).toString("base64url");
  return `${header}.${payload}.synthetic-signature`;
}

export const SYNTHETIC_TOKENS = {
  login: token("synthetic-ui-user", "password"),
  switch: token("synthetic-ui-user-two", "password"),
  recovery: token("synthetic-ui-user", "recovery"),
};
