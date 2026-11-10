"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { PersonalApiKeySummary } from "../../lib/evidence-api";

type WorkspaceOption = {
  id: string;
  name: string;
  role: "owner" | "admin" | "member";
  workspace_type?: "personal" | "group";
};

type CreatedKey = PersonalApiKeySummary & { token: string };

export default function PersonalApiKeys({ workspaces }: { workspaces: WorkspaceOption[] }) {
  const [keys, setKeys] = useState<PersonalApiKeySummary[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? "");
  const [name, setName] = useState("");
  const [expirationDays, setExpirationDays] = useState("90");
  const [created, setCreated] = useState<CreatedKey | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/evidence/me/api-keys", { cache: "no-store" });
      if (!response.ok) {
        setLoadState("unavailable");
        return;
      }
      const payload = (await response.json()) as unknown;
      if (!Array.isArray(payload)) {
        setLoadState("unavailable");
        return;
      }
      setKeys(payload as PersonalApiKeySummary[]);
      setLoadState("ready");
    } catch {
      setLoadState("unavailable");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!workspaceId && workspaces[0]) setWorkspaceId(workspaces[0].id);
  }, [workspaceId, workspaces]);

  async function createKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    setError("");
    setCreated(null);
    try {
      const response = await fetch("/api/evidence/me/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          name,
          expiration_days: Number(expirationDays),
        }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setError(payload?.error ?? "The API key could not be created.");
        return;
      }
      const key = payload as CreatedKey;
      setCreated(key);
      setName("");
      setConfirmRevoke(null);
      setMessage("API key created. Copy it now; it cannot be shown again.");
      await load();
    } catch {
      setError("The API key could not be created. Try again later.");
    } finally {
      setPending(false);
    }
  }

  async function revokeKey(keyId: string) {
    if (confirmRevoke !== keyId) {
      setConfirmRevoke(keyId);
      setMessage("");
      setError("");
      return;
    }
    setPending(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch(`/api/evidence/me/api-keys/${keyId}`, { method: "DELETE" });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setError(payload?.error ?? "The API key could not be revoked.");
        return;
      }
      setConfirmRevoke(null);
      setMessage("API key revoked. It can no longer read workspace data.");
      setCreated((current) => (current?.id === keyId ? null : current));
      await load();
    } catch {
      setError("The API key could not be revoked. Try again later.");
    } finally {
      setPending(false);
    }
  }

  async function copyToken() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.token);
      setMessage("API key copied to the clipboard.");
    } catch {
      setError("The key could not be copied. Select it and copy manually.");
    }
  }

  const workspaceName = (id: string) => workspaces.find((workspace) => workspace.id === id)?.name ?? id;

  return (
    <section id="personal-api-keys" className="workspace-panel" aria-labelledby="personal-api-keys-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">Machine access</p>
          <h2 id="personal-api-keys-heading">Personal API keys</h2>
        </div>
        <span className="workspace-role">Read only</span>
      </div>
      <p className="workspace-note">
        Keys can read evidence records and documented relationships in one workspace. They cannot
        read source bytes, write records, manage members, or perform account operations. Each key
        is shown once, expires within 90 days, and is limited to 60 reads per minute.
      </p>

      <form className="workspace-form" onSubmit={createKey}>
        <label htmlFor="api-key-workspace">Workspace</label>
        <select
          id="api-key-workspace"
          value={workspaceId}
          onChange={(event) => setWorkspaceId(event.target.value)}
          disabled={pending || workspaces.length === 0}
          required
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name} · {workspace.role}
            </option>
          ))}
        </select>
        <label htmlFor="api-key-name">Key name</label>
        <input
          id="api-key-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={80}
          placeholder="Reporting export"
          required
          disabled={pending}
        />
        <label htmlFor="api-key-expiry">Expires after</label>
        <select
          id="api-key-expiry"
          value={expirationDays}
          onChange={(event) => setExpirationDays(event.target.value)}
          disabled={pending}
        >
          <option value="30">30 days</option>
          <option value="60">60 days</option>
          <option value="90">90 days</option>
        </select>
        <button type="submit" disabled={pending || !workspaceId || !name.trim()}>
          {pending ? "Creating…" : "Create API key"}
        </button>
      </form>

      {created && (
        <div className="api-key-token" role="status">
          <p><strong>Copy this key now.</strong> It will not be shown again.</p>
          <code>{created.token}</code>
          <button type="button" onClick={copyToken} disabled={pending}>Copy key</button>
        </div>
      )}

      {message && <p className="workspace-message" role="status">{message}</p>}
      {error && <p className="workspace-message" role="alert">{error}</p>}

      {loadState === "loading" && <p>Loading API keys…</p>}
      {loadState === "unavailable" && <p role="alert">API keys are unavailable right now.</p>}
      {loadState === "ready" && keys.length === 0 && (
        <p className="workspace-note">No API keys have been created for this account.</p>
      )}
      {loadState === "ready" && keys.length > 0 && (
        <ul className="api-key-list">
          {keys.map((key) => {
            const revoked = key.revoked_at !== null;
            const expired = Date.parse(key.expires_at) <= Date.now();
            return (
              <li key={key.id}>
                <div>
                  <strong>{key.name}</strong>
                  <span>{workspaceName(key.workspace_id)} · {key.key_prefix}…</span>
                  <small>
                    {revoked
                      ? "Revoked"
                      : expired
                        ? "Expired"
                        : `Expires ${new Date(key.expires_at).toLocaleDateString()}`}
                    {key.last_used_at
                      ? ` · Last used ${new Date(key.last_used_at).toLocaleString()}`
                      : " · Never used"}
                  </small>
                </div>
                {!revoked && (
                  <button
                    type="button"
                    onClick={() => revokeKey(key.id)}
                    disabled={pending}
                  >
                    {confirmRevoke === key.id ? "Confirm revoke" : "Revoke"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
