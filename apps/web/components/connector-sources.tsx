"use client";

import { useCallback, useEffect, useState } from "react";

import {
  githubConnectorEnabled as resolveGitHubConnectorEnabled,
  googleDriveConnectorEnabled as resolveGoogleDriveConnectorEnabled,
  notionConnectorEnabled as resolveNotionConnectorEnabled,
} from "../lib/connector-flags";

type ConnectorConnection = {
  connection_id: string;
  provider: string;
  workspace_id: string | null;
  account_email: string | null;
  status: string;
  last_sync_at: string | null;
  last_error_category: string | null;
};

const CONNECTOR_FEEDBACK: Record<string, Record<string, string>> = {
  google_drive: {
    connected: "Google Drive is connected.",
    coming_soon: "Google Drive connections are not available right now.",
    configuration: "Google Drive connection is not configured.",
    denied: "Google Drive authorization was cancelled or could not be verified.",
    retry: "Two-factor authentication is verified. Retry connecting Google Drive.",
    store: "Google Drive was authorized, but its connection could not be saved.",
    token: "Google Drive authorization could not be completed. Try again.",
    unavailable: "Google Drive identity verification is unavailable. Try again later.",
  },
  notion: {
    connected: "Notion is connected.",
    coming_soon: "Notion connections are not available right now.",
    configuration: "Notion connection is not configured.",
    denied: "Notion authorization was cancelled or could not be verified.",
    retry: "Two-factor authentication is verified. Retry connecting Notion.",
    store: "Notion was authorized, but its connection could not be saved.",
    token: "Notion authorization could not be completed. Try again.",
    unavailable: "Notion identity verification is unavailable. Try again later.",
  },
  github: {
    connected: "GitHub is connected.",
    coming_soon: "GitHub connections are not available right now.",
    configuration: "GitHub connection is not configured.",
    denied: "GitHub authorization was cancelled or could not be verified.",
    retry: "Two-factor authentication is verified. Retry connecting GitHub.",
    store: "GitHub was authorized, but its connection could not be saved.",
    unavailable: "GitHub identity verification is unavailable. Try again later.",
  },
};

export default function ConnectorSources() {
  const [connectors, setConnectors] = useState<ConnectorConnection[]>([]);
  const [connectorsState, setConnectorsState] = useState<
    "loading" | "ready" | "unavailable"
  >("loading");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [callbackMessage, setCallbackMessage] = useState("");
  const googleDriveConnectorEnabled = resolveGoogleDriveConnectorEnabled({
    flag: process.env.NEXT_PUBLIC_GOOGLE_DRIVE_CONNECTOR_ENABLED,
  });
  const notionConnectorEnabled = resolveNotionConnectorEnabled({
    flag: process.env.NEXT_PUBLIC_NOTION_CONNECTOR_ENABLED,
  });
  const githubConnectorEnabled = resolveGitHubConnectorEnabled({
    flag: process.env.NEXT_PUBLIC_GITHUB_CONNECTOR_ENABLED,
  });

  const loadConnectors = useCallback(async (): Promise<void> => {
    setConnectorsState("loading");
    try {
      const response = await fetch("/api/evidence/connectors", { cache: "no-store" });
      if (!response.ok) {
        setConnectors([]);
        setConnectorsState("unavailable");
        return;
      }
      const payload: unknown = await response.json();
      if (!Array.isArray(payload)) {
        setConnectors([]);
        setConnectorsState("unavailable");
        return;
      }
      setConnectors(payload as ConnectorConnection[]);
      setConnectorsState("ready");
    } catch {
      setConnectors([]);
      setConnectorsState("unavailable");
    }
  }, []);

  const syncConnector = useCallback(async (connectionId: string): Promise<void> => {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch(
        `/api/evidence/connectors/${encodeURIComponent(connectionId)}/sync`,
        { method: "POST" },
      );
      if (!response.ok) {
        setMessage("The connector sync could not be completed.");
        return;
      }
      const payload = (await response.json()) as { ingested?: number; skipped?: number };
      setMessage(`Sync complete: ${payload.ingested ?? 0} documents ingested, ${payload.skipped ?? 0} skipped.`);
      await loadConnectors();
    } catch {
      setMessage("The connector service is unavailable.");
    } finally {
      setPending(false);
    }
  }, [loadConnectors]);

  const disconnectConnector = useCallback(async (connectionId: string): Promise<void> => {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch(
        `/api/evidence/connectors/${encodeURIComponent(connectionId)}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        setMessage("The connector could not be disconnected.");
        return;
      }
      setMessage("Connector disconnected and stored connector documents removed.");
      await loadConnectors();
    } catch {
      setMessage("The connector service is unavailable.");
    } finally {
      setPending(false);
    }
  }, [loadConnectors]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.get("connector");
    const status = params.get("status");
    if (provider && status) setCallbackMessage(CONNECTOR_FEEDBACK[provider]?.[status] ?? "");
    if (provider) void loadConnectors();
  }, [loadConnectors]);

  return (
    <section className="workspace-panel" aria-labelledby="connectors-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">Evidence sources</p>
          <h2 id="connectors-heading">Connected sources</h2>
        </div>
        <span className="workspace-role">Personal</span>
      </div>
      <p className="workspace-note">
        Connect a read-only source, then run a bounded sync. GitHub documents are bridged
        into your personal workspace evidence store so retrieve can use them. No files are
        written back to the provider.
      </p>
      <div className="workspace-form-actions">
        {googleDriveConnectorEnabled ? (
          <a className="workspace-secondary-action" href="/api/connectors/google-drive/start">
            Connect Google Drive
          </a>
        ) : (
          <button type="button" disabled>Google Drive · Coming soon</button>
        )}
        {notionConnectorEnabled ? (
          <a className="workspace-secondary-action" href="/api/connectors/notion/start">
            Connect Notion
          </a>
        ) : (
          <button type="button" disabled>Notion · Coming soon</button>
        )}
        {githubConnectorEnabled ? (
          <a className="workspace-secondary-action" href="/api/connectors/github/start">
            Connect GitHub
          </a>
        ) : (
          <button type="button" disabled>GitHub · Coming soon</button>
        )}
        <button type="button" disabled={pending} onClick={() => void loadConnectors()}>
          Refresh sources
        </button>
      </div>
      {connectorsState === "loading" && <p aria-live="polite">Loading connected sources…</p>}
      {connectorsState === "unavailable" && (
        <p role="alert">Connected sources are unavailable right now. Refresh to try again.</p>
      )}
      {connectorsState === "ready" && connectors.length === 0 && (
        <p className="workspace-note">No source is connected.</p>
      )}
      {connectors.map((connection) => (
        <div key={connection.connection_id} className="workspace-form-actions">
          <span className="workspace-note">
            {connection.provider}: {connection.status}
            {connection.account_email ? ` (${connection.account_email})` : ""}
            {connection.workspace_id ? ` · workspace ${connection.workspace_id}` : ""}
            {connection.last_sync_at ? ` · last sync ${new Date(connection.last_sync_at).toLocaleString()}` : ""}
          </span>
          <button type="button" disabled={pending} onClick={() => void syncConnector(connection.connection_id)}>
            Sync {connection.provider === "google_drive" ? "Drive" : connection.provider}
          </button>
          <button type="button" disabled={pending} onClick={() => void disconnectConnector(connection.connection_id)}>
            Disconnect
          </button>
        </div>
      ))}
      {callbackMessage && <p className="workspace-message" role="status">{callbackMessage}</p>}
      {message && <p className="workspace-message" role="status">{message}</p>}
    </section>
  );
}
