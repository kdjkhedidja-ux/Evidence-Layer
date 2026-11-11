import type { Metadata } from "next";
import Link from "next/link";
import { PublicSiteShell } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Documentation | Evidence Layer",
  description: "Architecture, evidence selection, provenance, evaluation, security boundaries, and token-cost trade-offs in Evidence Layer.",
};

export default function DocumentationPage() {
  return (
    <PublicSiteShell
      currentPath="/documentation"
      description="Evidence Layer is an evidence-selection layer between a retrieval system and the model that generates the answer. This guide distinguishes implemented code from service capabilities that remain disabled or planned."
      eyebrow="Product guide · Architecture"
      title="Documentation"
    >
      <section className="public-callout" aria-labelledby="docs-short-version">
        <p className="public-eyebrow">In one sentence</p>
        <h2 id="docs-short-version">Evidence Layer selects and traces evidence; it does not write the final answer.</h2>
        <p>
          A retrieval source or caller supplies candidate passages. Evidence Layer scores and ranks
          those candidates under a configured policy, then returns a smaller set with source identity
          and provenance so the caller’s language model can compose its own response.
        </p>
      </section>

      <section className="public-section" aria-labelledby="docs-flow">
        <h2 id="docs-flow">How the flow works</h2>
        <ol className="public-steps">
          <li><strong>Receive a task and eligible candidates.</strong> A retrieval layer or caller supplies a query and candidate records. A production source connector and hosted retrieval API are not part of the current service.</li>
          <li><strong>Score the candidates.</strong> The Python evidence core includes deterministic lexical scoring and a JEV scorer behind provider-selection policy. JEV use requires explicit policy enablement and a separately trusted provider-transfer authorisation; supported package paths currently fail closed for live transfer.</li>
          <li><strong>Rank and select evidence.</strong> Results are sorted and a configured evidence budget can return a focused subset. When a JEV result cannot be safely used, the policy recomputes the entire candidate set lexically rather than mixing scores.</li>
          <li><strong>Return evidence with provenance.</strong> The schemas preserve candidate identifiers and source references so callers can trace selected material back to the supplied evidence.</li>
          <li><strong>Generate the answer elsewhere.</strong> The caller’s chosen LLM consumes the selected context and writes the final answer. Evidence Layer does not contain a general-purpose answer-generation provider.</li>
        </ol>
      </section>

      <section className="public-section" aria-labelledby="docs-capabilities">
        <h2 id="docs-capabilities">Implemented, constrained, and planned</h2>
        <div className="public-card-grid">
          <article className="public-card">
            <h3>Implemented in the repository</h3>
            <ul>
              <li>Python schemas, a lexical scorer, ranking, and evaluation metrics.</li>
              <li>A JEV adapter and provider policy with whole-request lexical fallback behavior.</li>
              <li>A local stdio MCP tool named <code>retrieve_evidence</code>.</li>
              <li>Real-World and synthetic evaluation data, frozen manifests, result hashes, and offline verification scripts.</li>
              <li>An authenticated Next.js workspace with Supabase Auth, personal and organization workspaces, evidence and relationship CRUD, verified source-catalogue reads, read-only personal API keys, account deletion, and public ROI/evaluation pages.</li>
            </ul>
          </article>
          <article className="public-card public-card--muted">
            <h3>Constrained or unavailable in this application</h3>
            <ul>
              <li>The standard local MCP path is lexical-only and the corpus source boundary denies reads by default unless a trusted source boundary is injected.</li>
              <li>Live JEV transfer is not enabled on the supported application package paths.</li>
              <li>The hosted service stores workspace evidence metadata but exposes no retrieval/scoring endpoint or source ingestion/publish flow. A narrowed account/personal export is available with single-use, expiring download grants. Shared/group export and general download/CDN delivery are not available.</li>
              <li>The local deterministic source-routing library is not wired into a service.</li>
            </ul>
          </article>
          <article className="public-card public-card--muted">
            <h3>Planned or requiring a separate release</h3>
            <ul>
              <li>Production source connectors (a read-only connector SDK exists, but OAuth, token storage, sync, ingestion, and UI are not available), hosted retrieval, and a customer-data service.</li>
              <li>Provider transfer authorisation and live JEV use in an authorised service path.</li>
              <li>Integrated source routing, account usage reporting, billing, and a customer Comparison Lab.</li>
              <li>A supported final-answer generation integration; callers currently own that step.</li>
            </ul>
          </article>
        </div>
      </section>

      <section className="public-section" aria-labelledby="docs-provenance">
        <h2 id="docs-provenance">Evidence provenance and verification</h2>
        <p>
          Candidate IDs and source references travel through the schemas and selection results so a
          caller can identify where selected evidence came from. Evaluation manifests record input
          and output fingerprints; offline verifiers recompute saved rankings and metrics without
          provider calls. These repository checks verify the saved experiment artifacts. They do not
          independently verify the truth of source material or every future customer connector.
        </p>
        <p>
          Source access and provider transfer are separate security decisions. A valid source-read
          boundary must establish access before reading protected content. Sending query or candidate
          text to a scorer requires a separate trusted transfer authorisation. Workspace identity,
          source ACLs, safe read boundaries, and fail-closed provider policy are distinct controls;
          a ranking result does not grant access to any source.
        </p>
      </section>

      <section className="public-section" aria-labelledby="docs-efficiency">
        <h2 id="docs-efficiency">Token efficiency and cost optimisation</h2>
        <p>
          A focused evidence set can reduce repeated or irrelevant context sent to a downstream model.
          In the Phase 7 experiments, Evidence Layer passed three selected passages while the
          traditional baseline passed five. Fewer input passages reduced total tokens in those runs,
          but this does not guarantee savings on another dataset or model.
        </p>
        <p>
          Selection and scoring also consume compute. A remote scorer can add API usage and latency;
          local ranking consumes CPU time. Total cost should include retrieval, scoring, retries,
          answer generation, and operations. Token savings alone do not establish lower system cost,
          and shorter context does not prove equal or better evidence coverage. See the separate
          <Link href="/cost-performance">Cost &amp; Performance report</Link> for results by experiment.
        </p>
      </section>

      <section className="public-section" aria-labelledby="docs-integrations">
        <h2 id="docs-integrations">Integrations and security boundaries</h2>
        <p>
          The hosted product includes Supabase Auth, RLS-backed personal and organization
          workspaces, evidence/relationship/source-catalogue operations, read-only personal API
          keys, and an operational account-deletion worker. The FastAPI service is the
          identity/data boundary, not the evidence-retrieval or scoring endpoint. Google Drive
          ingestion, a production source router, a customer-facing retrieval/scoring API, export,
          mediated download links, and an LLM answer provider are not implemented in the current
          service. Phase 8 is not closed.
        </p>
        <p>
          Provider keys belong on a trusted server boundary. Source-access permission does not
          authorise sending content to a third party; transfer must be separately authorised and
          minimised. When remote scoring is unavailable or returns incomplete or invalid results,
          the policy must either recompute all scores locally or fail closed, according to the
          configured boundary.
        </p>
      </section>
    </PublicSiteShell>
  );
}
