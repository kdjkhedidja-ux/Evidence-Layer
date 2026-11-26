import type { Metadata } from "next";
import { PublicationBlockers, PublicSiteShell } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Terms of Use | Evidence Layer",
  description: "Draft terms for use of the Evidence Layer website and evaluation materials, with unconfirmed legal and commercial details marked for publication.",
};

const publicationBlockers = [
  "Legal name and address of the service operator: [insert verified details].",
  "Contracting entity, support/legal contact, and effective date: [insert verified details].",
  "Governing law and competent courts: [insert jurisdiction after legal review].",
  "Confirm whether and when an actual hosted service is offered, and replace draft service descriptions accordingly.",
  "No subscription, fees, quotas, refunds, service levels, or support terms have been approved; define them before accepting paid or production use.",
];

export default function TermsPage() {
  return (
    <PublicSiteShell
      currentPath="/terms"
      description="These draft terms describe the current website and the intended boundaries of a future Evidence Layer service. They are not a complete contract while the publication blockers remain."
      eyebrow="Legal · Service terms"
      title="Terms of Use"
    >
      <p className="public-updated">Effective date: [insert publication date] · Status: draft pending operator and legal review</p>
      <PublicationBlockers items={publicationBlockers} />

      <section className="public-section" aria-labelledby="terms-scope">
        <h2 id="terms-scope">1. Scope</h2>
        <p>
          Evidence Layer is a software project for selecting, ranking, and tracing supplied evidence
          for use by retrieval-augmented generation systems. The public website includes an
          authenticated workspace explorer and research documentation. Workspace records shown there
          are returned by the server after account verification; they are not a synthetic sample.
          The explorer is not a hosted customer-document retrieval or answer-generation service. These terms must be revised if
          the scope, operator, or available features change.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-use">
        <h2 id="terms-use">2. Acceptable use</h2>
        <p>You may use the public website and materials only in a lawful and authorised way. You must not:</p>
        <ul>
          <li>attempt unauthorised access, disrupt the site, evade access controls, or probe systems without written authorisation;</li>
          <li>submit or process material unless you have the necessary rights, notices, permissions, and authority;</li>
          <li>use evidence selection or automated output as the sole basis for a decision where a human review or legally required process is needed;</li>
          <li>use the service to violate privacy, intellectual-property, confidentiality, export-control, or other applicable rules;</li>
          <li>misrepresent benchmark findings, source provenance, or the status of an evaluation.</li>
        </ul>
      </section>

      <section className="public-section" aria-labelledby="terms-responsibilities">
        <h2 id="terms-responsibilities">3. Customer responsibilities and data rights</h2>
        <p>
          Before any future source connection or hosted retrieval feature is offered, the customer
          must have authority to access and process the supplied sources and to instruct the service
          to process them. The customer remains responsible for source permissions, notices to data
          subjects, the lawfulness and accuracy of supplied data, and reviewing evidence before use.
          Detailed processor terms and deletion/retention instructions must be agreed separately
          where the service acts as a processor.
        </p>
        <p>
          Backups are logical dumps, not point-in-time recovery, and do not cover all provider-managed
          data. A restore may reintroduce deleted records; restored access remains blocked until
          deletion reconciliation completes.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-third-party">
        <h2 id="terms-third-party">4. Third-party providers and external services</h2>
        <p>
          The repository contains authentication/workspace integrations and separate evaluation
          tooling. Historical Phase 7 and Phase 7B experiments used OpenAI and DeepSeek for test
          answer generation, and TypeSafe/JEV for evidence scoring. Those experiments used the fixed
          public Real-World v1 benchmark data. They do not constitute an active answer-generation
          integration for the public Evidence Explorer. Any future provider connection, data transfer,
          provider terms, processing location, or subprocessor must be disclosed and authorised before
          customer data is sent.
        </p>
        <p>
          External providers operate under their own terms and availability. The operator must
          publish a verified list of providers and applicable terms before offering an integrated
          customer service.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-ai">
        <h2 id="terms-ai">5. Evidence selection and AI output</h2>
        <p>
          Evidence Layer selects and ranks evidence; it is not itself the answer-generating language
          model. Selection can omit relevant context, include irrelevant material, or reflect limits
          in the query, source corpus, scorer, and policy. If a customer or third-party model generates
          an answer, that answer may be inaccurate, incomplete, outdated, biased, or unsupported.
          Users must check sources, citations, and material claims before relying on an output.
        </p>
        <p>
          The product makes no guarantee of a particular percentage of token savings, monetary
          savings, latency improvement, retrieval quality, answer correctness, completeness, or
          citation quality. Evaluation results are specific to the disclosed benchmark and do not
          guarantee production performance.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-usage">
        <h2 id="terms-usage">6. Usage and estimated costs</h2>
        <p>
          A future service may measure token counts, request duration, retrieval volume, evidence
          selection, retries, and estimated provider costs. Cost estimates may use returned token
          counts and published rate cards. They are not invoices or guaranteed charges: provider
          billing may vary with model, region, cache, service tier, pricing changes, retries, taxes,
          or account-specific terms. Any subscription, quota, overage, refund, or payment term must
          be presented and agreed before that feature is offered. No such commercial terms are
          confirmed by this repository.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-suspension">
        <h2 id="terms-suspension">7. Suspension and termination</h2>
        <p>
          For a future hosted service, the operator may need to restrict or suspend access to address
          a security threat, unlawful use, material breach, provider outage, or legal requirement.
          The final contract must define notice, cure periods, data export/deletion, and effects of
          termination. The current repository does not establish those commercial processes.
        </p>
        <p>
          <strong>Account deletion requests.</strong> A signed-in person may request deletion of the
          account from the account panel. Accepting a request blocks access immediately and is not
          reversible in the current implementation. The deletion worker then removes the account&apos;s
          personal workspace data, verified source snapshots, queued Storage objects, and the
          authentication identity, and records a terminal <code>purged</code> state. Shared-workspace
          data and other members&apos; records are not deleted by an account-scoped request, and
          provider backups and point-in-time recovery copies are not proven erased. See the privacy
          notice for the full scope.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-liability">
        <h2 id="terms-liability">8. Warranties and liability</h2>
        <p>
          The final terms must describe service availability, warranties, liability allocation, and
          any permitted limitation in a manner reviewed under the chosen governing law. Nothing in
          the final terms may exclude or limit liability where applicable law does not permit that
          exclusion or limitation. No service level or guaranteed availability is currently defined.
        </p>
      </section>

      <section className="public-section" aria-labelledby="terms-law">
        <h2 id="terms-law">9. Governing law and contact</h2>
        <p>
          Governing law and venue: <strong>[insert after confirming the contracting entity and obtaining legal review]</strong>.
          Operator contact: <strong>[insert verified legal contact and address]</strong>. These details
          are publication blockers and have deliberately not been inferred from repository or account
          information.
        </p>
      </section>
    </PublicSiteShell>
  );
}
