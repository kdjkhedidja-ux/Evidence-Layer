"use client";

import { useState } from "react";

import { remoteMcpEnabled, remoteMcpWorkspaceUrl } from "../lib/remote-mcp";

type WorkspaceOption = {
  id: string;
  name: string;
};

export default function RemoteMcp({ workspace }: { workspace: WorkspaceOption | null }) {
  const enabled = remoteMcpEnabled({ flag: process.env.NEXT_PUBLIC_REMOTE_MCP_ENABLED });
  const url = remoteMcpWorkspaceUrl({
    baseUrl: process.env.NEXT_PUBLIC_REMOTE_MCP_URL,
    workspaceId: workspace?.id,
  });
  const [message, setMessage] = useState("");

  const config = url
    ? JSON.stringify(
        {
          mcpServers: {
            "evidence-layer": {
              type: "http",
              url,
              headers: { Authorization: "Bearer <YOUR_PERSONAL_API_KEY>" },
            },
          },
        },
        null,
        2,
      )
    : "";

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setMessage(`${label} copied.`);
    } catch {
      setMessage("The value could not be copied. Select it and copy manually.");
    }
  }

  return (
    <section id="remote-mcp" className="workspace-panel" aria-labelledby="remote-mcp-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">Agent access</p>
          <h2 id="remote-mcp-heading">Remote MCP</h2>
        </div>
        <span className="workspace-role">Hosted</span>
      </div>
      <p className="workspace-note">
        Connect a remote MCP client with a hosted URL and a personal API key. No local Python
        server or package installation is required. The key stays in your MCP client configuration
        and can be revoked from Personal API keys at any time.
      </p>

      {!enabled && (
        <p className="workspace-note">
          Remote MCP is not enabled in this environment.
        </p>
      )}

      {enabled && !workspace && (
        <p className="workspace-note">
          Select a workspace to generate its hosted MCP URL.
        </p>
      )}

      {enabled && workspace && !url && (
        <p className="workspace-message" role="alert">
          Remote MCP URL is not configured. Set NEXT_PUBLIC_REMOTE_MCP_URL for the web deployment.
        </p>
      )}

      {enabled && workspace && url && (
        <>
          <div className="workspace-mcp-endpoint">
            <code>{url}</code>
            <button type="button" onClick={() => void copy(url, "MCP URL")}>Copy URL</button>
          </div>
          <ol className="workspace-mcp-steps">
            <li>Create or copy a read-only personal API key in <a href="#personal-api-keys">Personal API keys</a>.</li>
            <li>Use the hosted URL above in your MCP client's remote HTTP transport.</li>
            <li>Send <code>Authorization: Bearer &lt;YOUR_PERSONAL_API_KEY&gt;</code> with every request.</li>
          </ol>
          <div className="workspace-mcp-config">
            <div className="workspace-section-heading">
              <h3>MCP client configuration</h3>
              <button type="button" onClick={() => void copy(config, "MCP configuration")}>Copy config</button>
            </div>
            <pre>{config}</pre>
          </div>
          <p className="workspace-note">
            The endpoint is read-only and exposes <code>retrieve_evidence</code> and{" "}
            <code>get_document</code>. Workspace ownership and key scope are enforced by the
            Evidence Service; provider source bytes are never returned.
          </p>
        </>
      )}

      {message && <p className="workspace-message" role="status">{message}</p>}
    </section>
  );
}
