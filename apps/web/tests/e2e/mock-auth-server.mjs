import { createServer } from "node:http";

const host = "127.0.0.1";
const port = 54321;
const accessTokens = new Map([
  ["synthetic-login-access-token", "person@example.test"],
  ["synthetic-switch-access-token", "switch@example.test"],
  ["synthetic-recovery-access-token", "person@example.test"],
]);

function user(email = "person@example.test") {
  const isSecondAccount = email === "switch@example.test";
  return {
    id: isSecondAccount ? "synthetic-ui-user-two" : "synthetic-ui-user",
    aud: "authenticated",
    role: "authenticated",
    email: isSecondAccount ? "switch@example.test" : "person@example.test",
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    created_at: "2026-01-01T00:00:00.000Z",
  };
}

function session(accessToken, email = "person@example.test") {
  return {
    access_token: accessToken,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: `synthetic-refresh-${accessToken}`,
    user: user(email),
  };
}

function sendJson(response, status, body) {
  response.writeHead(status, {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
  });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${port}`);
  if (request.method === "OPTIONS") {
    response.writeHead(204, {
      "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info, x-supabase-api-version",
      "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
      "Access-Control-Allow-Origin": "*",
    });
    response.end();
    return;
  }
  if (url.pathname === "/health") {
    response.writeHead(204, { "Access-Control-Allow-Origin": "*" });
    response.end();
    return;
  }
  if (!url.pathname.startsWith("/auth/v1/")) {
    sendJson(response, 404, { message: "Synthetic Auth fixture: route not found." });
    return;
  }
  const endpoint = url.pathname.slice("/auth/v1".length);
  if (endpoint === "/user" && request.method === "GET") {
    const token = request.headers.authorization?.replace(/^Bearer /, "");
    const email = token ? accessTokens.get(token) : undefined;
    if (!email) {
      sendJson(response, 401, { message: "Synthetic session not found.", code: "invalid_token" });
      return;
    }
    sendJson(response, 200, user(email));
    return;
  }

  if (endpoint === "/token" && url.searchParams.get("grant_type") === "password") {
    let body;
    try {
      body = await readJson(request);
    } catch {
      sendJson(response, 400, { message: "Invalid synthetic request." });
      return;
    }
    if (body.password === "incorrect synthetic password") {
      sendJson(response, 400, { message: "Invalid login credentials.", code: "invalid_credentials" });
      return;
    }
    const secondAccount = body.email === "switch@example.test";
    const accessToken = secondAccount ? "synthetic-switch-access-token" : "synthetic-login-access-token";
    sendJson(response, 200, session(accessToken, secondAccount ? "switch@example.test" : "person@example.test"));
    return;
  }

  if (endpoint === "/signup" && request.method === "POST") {
    sendJson(response, 200, { ...user(), email: "person@example.test", confirmation_sent_at: "2026-01-01T00:00:00.000Z" });
    return;
  }
  if (endpoint === "/recover" && request.method === "POST") {
    sendJson(response, 200, {});
    return;
  }
  if (endpoint === "/verify" && request.method === "POST") {
    let body;
    try {
      body = await readJson(request);
    } catch {
      sendJson(response, 400, { message: "Invalid synthetic recovery request.", code: "otp_expired" });
      return;
    }
    if (body.token_hash !== "synthetic-recovery-link") {
      sendJson(response, 400, { message: "Synthetic recovery link is expired or malformed.", code: "otp_expired" });
      return;
    }
    sendJson(response, 200, session("synthetic-recovery-access-token"));
    return;
  }
  if (endpoint === "/user" && request.method === "PUT") {
    sendJson(response, 200, user());
    return;
  }
  if (endpoint === "/logout" && request.method === "POST") {
    response.writeHead(204, { "Access-Control-Allow-Origin": "*" });
    response.end();
    return;
  }

  sendJson(response, 404, { message: "Synthetic Auth fixture: route not found." });
});

function readJson(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

server.listen(port, host);
