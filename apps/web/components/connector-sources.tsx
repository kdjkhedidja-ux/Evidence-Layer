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
  account_email: string | null;
  status: string;
  last_sync_at: string | null;
  last_error_category: string | null;
};

export default function ConnectorSources() {
  const [connectors, setConnectors] = useState<ConnectorConnection[]>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
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
    try {
      const response = await fetch("/api/evidence/connectors", { cache: "no-store" });
      if (!response.ok) return;
      const payload = (await response.json()) as ConnectorConnection[];
      setConnectors(Array.isArray(payload) ? payload : []);
    } catch {
      // The connector panel is optional; leave the existing state intact.
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
    if (new URLSearchParams(window.location.search).get("connector")) {
      void loadConnectors();
    }
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
        Connect a read-only Notion or Google Drive source, then run a bounded sync. Documents are
        stored in your personal connector store; no files are written back to the provider.
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
      {connectors.length === 0 && <p className="workspace-note">No source is connected.</p>}
      {connectors.map((connection) => (
        <div key={connection.connection_id} className="workspace-form-actions">
          <span className="workspace-note">
            {connection.provider}: {connection.status}
            {connection.account_email ? ` (${connection.account_email})` : ""}
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
      {message && <p className="workspace-message" role="status">{message}</p>}
    </section>
  );
}
