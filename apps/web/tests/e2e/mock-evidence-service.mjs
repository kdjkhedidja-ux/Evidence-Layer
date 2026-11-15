import { createServer } from "node:http";
import { SYNTHETIC_TOKENS } from "./mock-tokens.mjs";

const host = "127.0.0.1";
// Keep this fixture away from Supabase's default local Postgres port (54322).
const port = 54329;
const accounts = new Map([
  [SYNTHETIC_TOKENS.login, {
    userId: "synthetic-ui-user",
    workspace: {
      id: "10000000-0000-4000-8000-000000000002",
      name: "First synthetic workspace",
      role: "owner",
    },
  }],
  [SYNTHETIC_TOKENS.switch, {
    userId: "synthetic-ui-user-two",
    workspace: {
      id: "10000000-0000-4000-8000-000000000004",
      name: "Second synthetic workspace",
      role: "owner",
    },
  }],
  [SYNTHETIC_TOKENS.recovery, {
    userId: "synthetic-ui-user",
    workspace: {
      id: "10000000-0000-4000-8000-000000000002",
      name: "First synthetic workspace",
      role: "owner",
    },
  }],
]);

function sendJson(response, status, body) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${port}`);
  if (url.pathname === "/health") {
    response.writeHead(204);
    response.end();
    return;
  }

  const token = request.headers.authorization?.replace(/^Bearer /, "");
  const account = token ? accounts.get(token) : undefined;
  if (!account) {
    sendJson(response, 401, { detail: "Synthetic identity was not authorized." });
    return;
  }

  if (url.pathname === "/v1/me" && request.method === "GET") {
    sendJson(response, 200, {
      user_id: account.userId,
      issuer: "https://synthetic.supabase.co/auth/v1",
      workspaces: [account.workspace],
    });
    return;
  }

  if (url.pathname === "/v1/me/personal-workspace" && request.method === "PUT") {
    sendJson(response, 200, account.workspace);
    return;
  }

  const workspaceMatch = url.pathname.match(/^\/v1\/workspaces\/([0-9a-f-]+)$/i);
  if (workspaceMatch && request.method === "GET") {
    if (workspaceMatch[1] !== account.workspace.id) {
      sendJson(response, 404, { detail: "Synthetic workspace was not found." });
      return;
    }
    sendJson(response, 200, account.workspace);
    return;
  }

  sendJson(response, 404, { detail: "Synthetic service route was not found." });
});

server.listen(port, host);
