import type { Metadata } from "next";
import { PublicationBlockers, PublicSiteShell, ResponsiveTable } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Privacy Policy | Evidence Layer",
  description: "How the Evidence Layer website and any future service may handle personal data, with publication details still to confirm.",
};

const publicationBlockers = [
  "Controller identity and postal address: [insert legal name and address].",
  "Privacy contact and, if appointed or required, Data Protection Officer: [insert verified contact details].",
  "Effective date, final retention periods, hosting locations, and complete processor/subprocessor list.",
  "Confirm the lawful basis for each live processing purpose and the applicable transfer safeguards with the actual vendors and account configuration.",
  "Inventory hosting cookies and browser storage, and add a consent mechanism before introducing non-essential cookies or tracking.",
];

export default function PrivacyPage() {
  return (
    <PublicSiteShell
      currentPath="/privacy"
      description="This draft explains the data flows visible in the current website code and identifies the facts the operator must confirm before a hosted service is offered."
      eyebrow="Legal · Data protection"
      title="Privacy Policy"
    >
      <p className="public-updated">Effective date: [insert publication date] · Status: draft pending the confirmations below</p>
      <PublicationBlockers items={publicationBlockers} />

      <section className="public-section" aria-labelledby="privacy-scope">
        <h2 id="privacy-scope">1. Scope and roles</h2>
        <p>
          Evidence Layer is the product name used by this website. The repository does not confirm
          the legal person or business that operates it. The operator must identify the controller
          for each processing activity before publication. Under the GDPR, a controller determines
          the purposes and means of processing; a processor handles personal data on a controller’s
          behalf under documented instructions. One organisation can have different roles for
          different activities.
        </p>
        <p>
          If a future customer connects source material, the customer may be controller for that
          material while Evidence Layer may act as processor for specified service operations. The
          actual roles depend on who determines each purpose and must be set out in the service
          agreement and, where required, a data processing agreement. Evidence Layer may separately
          act as controller for its own account administration and security purposes. These are
          role descriptions to assess, not a final assignment of legal roles.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-current">
        <h2 id="privacy-current">2. Current website and service state</h2>
        <p>
          The Evidence Explorer displays only workspace records that the server returns after
          verifying the signed-in account; it does not substitute synthetic records. The web code
          also contains Supabase Auth session and recovery flows and server-side workspace requests
          to an Evidence Service when the corresponding configuration is present. Those flows can involve an email address,
          account identifier, session data, workspace identifier, workspace name, and role. The
          operator must confirm which environments are live, what each vendor stores, and where it
          is processed before this text is published as a complete notice.
        </p>
        <p>
          The public website does not generate answers with OpenAI or DeepSeek and does not connect
          customer source documents to a language model. Historical benchmark runs used public
          Real-World v1 and separately frozen synthetic evaluation materials in offline evaluations. The JEV scoring
          data flow is described in section 7. Any further language-model, source connector, or
          hosted retrieval integration requires a reviewed data-flow notice before it is enabled. This policy makes no representation about a provider’s retention or training
          settings; those settings and contractual terms must be checked for each integration.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-categories">
        <h2 id="privacy-categories">3. Data categories and purposes</h2>
        <ResponsiveTable label="Personal data categories and purposes">
          <table className="public-table">
            <caption>Data and purposes visible now or relevant to a future hosted service</caption>
            <thead><tr><th scope="col">Category</th><th scope="col">Purpose</th><th scope="col">Current status</th></tr></thead>
            <tbody>
              <tr><th scope="row">Account data: email, account identifier, authentication and recovery session data</th><td>Sign-in, account recovery, session security, and responding to account requests</td><td>Authentication code exists; actual live configuration and retention require confirmation</td></tr>
              <tr><th scope="row">Workspace data: workspace identifiers, names, and membership role</th><td>Display or provision an authorised personal workspace</td><td>Server-side workspace API integration exists when configured; not source-document retrieval</td></tr>
              <tr><th scope="row">Request and device data: IP address, browser headers, timestamps, and security events</th><td>Website delivery, abuse prevention, troubleshooting, and security where collected by the host or service</td><td>Provider logging and retention are not established by this repository and must be confirmed</td></tr>
              <tr><th scope="row">Queries, candidate text, source references, and evidence selections</th><td>Potential future retrieval, ranking, provenance, and answer-context preparation</td><td>Not accepted by public pages; authenticated workspace content is handled by the service under the access boundary</td></tr>
              <tr><th scope="row">Operational metrics: token counts, request duration, retrieval volume, estimated costs, and evidence-selection statistics</th><td>Potential future usage reporting, reliability monitoring, cost estimation, and product evaluation</td><td>Not currently exposed as customer usage telemetry in the web app; may be personal data if linked to an account or run</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
      </section>

      <section className="public-section" aria-labelledby="privacy-lawful-basis">
        <h2 id="privacy-lawful-basis">4. GDPR legal bases</h2>
        <p>
          The controller must select and document an Article 6 basis for each actual purpose before
          processing. The table gives possibilities for assessment; it does not state which basis
          the unidentified operator currently relies on.
        </p>
        <ResponsiveTable label="Possible GDPR Article 6 legal bases requiring controller confirmation">
          <table className="public-table">
            <caption>Possible Article 6 bases to confirm for each processing purpose</caption>
            <thead><tr><th scope="col">Purpose</th><th scope="col">Possible Article 6 basis to assess</th><th scope="col">Required confirmation</th></tr></thead>
            <tbody>
              <tr><th scope="row">Provide a requested account or contracted feature</th><td>Article 6(1)(b), where processing is objectively necessary for a contract or pre-contract steps requested by the person</td><td>Confirm the service/contract and data necessity</td></tr>
              <tr><th scope="row">Protect sessions, prevent abuse, and maintain service security</th><td>Article 6(1)(f), where a documented legitimate-interest assessment supports the processing; Article 6(1)(c) only for a specific legal obligation</td><td>Document the purpose, necessity, balancing, and retention</td></tr>
              <tr><th scope="row">Optional analytics or marketing, if introduced</th><td>Article 6(1)(a) consent where appropriate; device storage/access rules may separately require consent under applicable ePrivacy law</td><td>Do not activate optional tracking before the required consent and controls exist</td></tr>
              <tr><th scope="row">Future customer-content retrieval or provider processing</th><td>Depends on the customer/controller relationship and the particular service purpose; Article 6 must be assessed per activity</td><td>Confirm roles, instructions, notices, contracts, and any required separate provider-transfer authorisation</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-source-note">Reference: <a href="https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng" rel="noreferrer">GDPR, Regulation (EU) 2016/679, Article 6</a>.</p>
      </section>

      <section className="public-section" aria-labelledby="privacy-minimisation">
        <h2 id="privacy-minimisation">5. Minimisation, retention, security, and access</h2>
        <p>
          Data should be limited to what is needed for a stated purpose, kept accurate where
          relevant, and deleted or anonymised when no longer needed. A final schedule must define
          retention for account records, recovery events, workspace metadata, access logs, support
          requests, source content, benchmark records, and backups: [insert confirmed periods and
          deletion process]. The actual host and provider retention settings must be included.
        </p>
        <p>
          Controls visible in the code include server-side identity verification, cookie-backed
          authentication, and server-side forwarding of a verified session token to workspace API
          routes. The source-read boundary defaults to deny access unless a trusted boundary is
          provided. This is not a certification or a complete security statement. The operator must
          verify encryption, production access, key management, backups, audit coverage, incident
          handling, and vendor controls before making service-wide security claims.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-rights">
        <h2 id="privacy-rights">6. Your rights and how to exercise them</h2>
        <p>
          Depending on the circumstances and applicable law, you may ask for access, rectification,
          erasure, restriction, or portability; object to processing based on legitimate interests;
          and withdraw consent where consent is the basis. You may also complain to the competent
          supervisory authority. Rights have conditions and exceptions under the GDPR.
        </p>
        <p>
          Send requests to <strong>[insert monitored privacy contact]</strong>. The controller must
          verify the request proportionately, respond within applicable time limits, and explain any
          refusal. If Evidence Layer is acting as a processor for a customer, direct the request to
          that customer/controller; the processor should assist as required by its agreement.
        </p>
        <p className="public-source-note">Reference: <a href="https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng" rel="noreferrer">GDPR, Articles 12–22</a>.</p>
      </section>

      <section className="public-section" aria-labelledby="privacy-transfers">
        <h2 id="privacy-transfers">7. Processors, providers, and international transfers</h2>
        <p>
          The repository identifies integrations with Supabase Auth and a configurable Evidence
          Service, and documents deployment of the website through Vercel. This is not a verified
          subprocessor register. Before publication, the operator must name each entity, purpose,
          processing location, retention, contractual role, and onward recipient. OpenAI and
          DeepSeek appear in benchmark tooling and are not connected to answer generation by the
          public website. Hosted retrieval (web task-context, API keys and Remote MCP) uses the
          TypeSafe Jev scorer by default: for each retrieval that uses it, the service sends the
          query and up to 50 candidate text snippets from the workspace, with technical snippet
          identifiers (no name, email, user or workspace identifier), to TypeSafe for relevance
          scoring. A caller can turn this off per request (<code>use_jev=false</code>); if scoring
          is off, unavailable, over the daily budget or fails, ranking falls back to local lexical
          scoring and nothing is sent. Per TypeSafe&apos;s published terms (checked 2026-11-27): the
          service is hosted in the United States; inputs are not used to train or fine-tune models;
          no fixed retention period is stated (data is kept as long as necessary for the purpose);
          TypeSafe may derive technical telemetry from requests; zero data retention is offered
          only to enterprise customers.
        </p>
        <p>
          If personal data is transferred outside the EEA, the controller and processor must identify
          a valid Chapter V transfer mechanism for the actual transfer. This may include an adequacy
          decision or appropriate safeguards such as Standard Contractual Clauses, with any required
          transfer assessment and supplementary measures. No destination, adequacy decision, or
          safeguard is asserted here because vendor regions and contracts have not been verified.
        </p>
        <p className="public-source-note">
          References: <a href="https://www.edpb.europa.eu/sme/be-compliant/international-data-transfers_en" rel="noreferrer">EDPB guidance on international transfers</a> and <a href="https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng" rel="noreferrer">GDPR Chapter V</a>.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-cookies">
        <h2 id="privacy-cookies">8. Cookies and browser storage</h2>
        <p>
          The authentication implementation uses cookies for session and recovery flows. The theme
          preference is stored in browser local storage. No analytics or advertising tag was found in
          the application source reviewed for this draft; the hosting platform and any future tools
          may have separate storage or logs that still need an inventory. Before adding non-essential
          tracking, the operator must provide clear information and obtain valid consent where
          required, with a way to change or withdraw it.
        </p>
        <p className="public-source-note">Reference: <a href="https://www.edpb.europa.eu/contact/frequently-asked-questions_en" rel="noreferrer">EDPB FAQ on cookies and consent</a>.</p>
      </section>

      <section className="public-section" aria-labelledby="privacy-deletion">
        <h2 id="privacy-deletion">9. Account deletion and data removal</h2>
        <p>
          A signed-in person can request account deletion from the account panel. The request is
          idempotent, and accepting it blocks access immediately; cancellation after acceptance is
          not offered. The deletion worker removes the account&apos;s personal workspace data,
          verified source snapshots and queued Storage objects, the authentication identity, and
          records a terminal <code>purged</code> state. Organization-owner deletion also removes the
          organization&apos;s group workspaces and snapshots.
        </p>
        <p>
          Shared-workspace data and other members&apos; records are not deleted by an account-scoped
          request; shared-workspace owner-unanimity deletion is not implemented. Provider backups,
          point-in-time recovery copies, and bytes already delivered to a client are not proven
          erased. Do not treat account deletion as a full provider-backup erasure guarantee.
          Current backups are logical dumps; provider point-in-time recovery is not enabled. A
          restore can reintroduce deleted records, and restored access remains blocked until
          deletion reconciliation completes.
        </p>
        <p>
          Connector users can disconnect a Google Drive source in the account panel. Disconnect
          removes the local connection and imported connector documents and attempts Google token
          revocation. Users can also revoke the app&apos;s access at
          <a href="https://myaccount.google.com/permissions" rel="noreferrer">Google Account permissions</a>.
          See <a href="/data-deletion">Data deletion instructions</a>.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-google-drive">
        <h2 id="privacy-google-drive">10. Google Drive connector data</h2>
        <p>
          When you choose to connect Google Drive, Evidence Layer requests the
          <code>drive.readonly</code> scope and accesses only files visible to the Google account
          you authorize. The connector is being prepared for a personal, read-only workflow. It
          does not write to Drive or modify your files.
        </p>
        <p>
          Google user data is used only to ingest the documents you select or sync into your private
          connector store so you can use them in your Evidence Layer workspace. It is not sold, used
          for advertising, or used to train generalized AI/ML models. It is not shared with third
          parties except infrastructure providers that process it on our behalf under contract.
          Human review is not performed except where necessary for security, abuse, or legal
          compliance.
        </p>
        <p>
          OAuth access and refresh tokens are encrypted at rest with a server-side key. Imported
          connector documents are protected by owner-only row-level security. Data is retained until
          you disconnect the connector or delete your account, subject to the provider backup
          limitations described above. Disconnect is available in the account panel; you can also
          revoke access from your Google Account.
        </p>
        <p>
          Evidence Layer&apos;s use of information received from Google APIs will adhere to the
          <a href="https://developers.google.com/terms/api-services-user-data-policy" rel="noreferrer">Google API Services User Data Policy</a>,
          including the Limited Use requirements.
        </p>
      </section>

      <section className="public-section" aria-labelledby="privacy-changes">
        <h2 id="privacy-changes">11. Changes and contact</h2>
        <p>
          Material changes to processing should be reflected here before the affected feature is
          activated. For privacy questions, contact <strong>[insert verified privacy contact]</strong>.
          The legal operator, postal address, effective date, and complaint contact remain
          publication blockers.
        </p>
      </section>
    </PublicSiteShell>
  );
}
