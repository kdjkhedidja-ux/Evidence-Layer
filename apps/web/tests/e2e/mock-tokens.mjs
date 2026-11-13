function token(subject, method) {
  const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({
    sub: subject,
    aud: "authenticated",
    role: "authenticated",
    aal: "aal1",
    amr: [{ method, timestamp: Math.floor(Date.now() / 1000) }],
    exp: Math.floor(Date.now() / 1000) + 3600,
    iat: Math.floor(Date.now() / 1000),
    session_id: `synthetic-session-${subject}`,
  })).toString("base64url");
  return `${header}.${payload}.synthetic-signature`;
}

export const SYNTHETIC_TOKENS = {
  login: token("synthetic-ui-user", "password"),
  switch: token("synthetic-ui-user-two", "password"),
  recovery: token("synthetic-ui-user", "recovery"),
};
