import type { Metadata } from "next";

import { PublicSiteShell } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Data Deletion | Evidence Layer",
  description: "How to disconnect a source, delete connector data, and request account deletion.",
};

export default function DataDeletionPage() {
  return (
    <PublicSiteShell
      currentPath="/data-deletion"
      description="Instructions for removing Google Drive connector data and deleting an Evidence Layer account."
      eyebrow="Legal · Data control"
      title="Data deletion"
    >
      <p className="public-updated">Status: operational instructions; legal entity/contact details remain a publication blocker.</p>

      <section className="public-section" aria-labelledby="deletion-connector">
        <h2 id="deletion-connector">Disconnect Google Drive</h2>
        <ol>
          <li>Sign in to Evidence Layer and open the account panel.</li>
          <li>In “Connected sources”, select <strong>Disconnect</strong> next to the Google Drive connection.</li>
          <li>Evidence Layer deletes the local connection and imported connector documents and attempts to revoke the stored Google token.</li>
          <li>To revoke access independently, open <a href="https://myaccount.google.com/permissions" rel="noreferrer">Google Account permissions</a>, select Evidence Layer, and remove access.</li>
        </ol>
      </section>

      <section className="public-section" aria-labelledby="deletion-account">
        <h2 id="deletion-account">Delete an account</h2>
        <ol>
          <li>Sign in and open the account panel.</li>
          <li>Create an account deletion request and accept it when prompted.</li>
          <li>Access is blocked immediately; the deletion worker removes personal-workspace data, snapshots, queued Storage objects, and the authentication identity, then records terminal <code>purged</code> status.</li>
          <li>Shared-workspace records and provider backups may remain; see the <a href="/privacy#privacy-deletion">privacy notice</a>.</li>
        </ol>
      </section>

      <section className="public-section" aria-labelledby="deletion-contact">
        <h2 id="deletion-contact">Contact</h2>
        <p>
          If you cannot access the account or need help with a deletion request, use the support
          contact published in the product footer. A monitored support/privacy contact must be
          confirmed before public Google OAuth verification.
        </p>
      </section>
    </PublicSiteShell>
  );
}
