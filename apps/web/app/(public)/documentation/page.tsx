import type { Metadata } from "next";
import Link from "next/link";
import { PublicSiteShell, ResponsiveTable } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Documentation | Evidence Layer",
  description: "What Evidence Layer is, how a task becomes bounded evidence, how agents connect, what is live, and where the security boundary sits.",
};

export default function DocumentationPage() {
  return (
    <PublicSiteShell
      currentPath="/documentation"
      eyebrow="Product guide · Architecture"
      title="Documentation"
      description="Evidence Layer prepares bounded, traceable evidence for an agent. It is not the agent and it does not write the final answer."
    >
      <section className="public-callout" aria-labelledby="docs-one-sentence">
        <p className="public-eyebrow">In one sentence</p>
        <h2 id="docs-one-sentence">Evidence Layer selects and traces evidence; the caller&apos;s model writes the answer.</h2>
        <p>
          A task or query enters with candidate evidence. Evidence Layer chunks and ranks those
          candidates, applies path and intent priorities, and returns a smaller context with source
          identity and provenance. The agent or application then sends that context to its own
          language model.
        </p>
      </section>

      <section className="public-section" aria-labelledby="docs-system">
        <h2 id="docs-system">The system at a glance</h2>
        <figure className="docs-figure">
          <div className="docs-diagram" role="img" aria-label="An agent or RAG system sends a task to Evidence Layer; Evidence Layer reads workspace evidence and optionally calls JEV scoring; it returns bounded selected evidence to the agent's language model.">
            <div className="docs-diagram-group">
              <div className="docs-node"><strong>Agent / RAG</strong><span>task + candidate evidence</span></div>
              <div className="docs-node docs-node--quiet"><strong>Workspace</strong><span>Supabase RLS evidence, relationships, sources</span></div>
            </div>
            <span className="docs-arrow" aria-hidden="true">→</span>
            <div className="docs-node docs-node--accent">
              <strong>Evidence Layer</strong>
              <span>chunk · rank · select · pack · provenance</span>
            </div>
            <span className="docs-arrow" aria-hidden="true">→</span>
            <div className="docs-node"><strong>Agent LLM</strong><span>writes the final answer</span></div>
          </div>
          <div className="docs-source-row">
            <span><strong>Sources:</strong> GitHub App, Notion, manual/API records</span>
            <span><strong>Optional scorer:</strong> TypeSafe JEV, request-opt-in and fail-closed</span>
            <span><strong>Agent access:</strong> API-key retrieve, Remote MCP, workspace task-context</span>
          </div>
          <figcaption>
            Evidence Layer owns selection and provenance. It does not execute the agent, call the
            user&apos;s answer model, or receive the agent&apos;s token bill.
          </figcaption>
        </figure>
      </section>

      <section className="public-section" aria-labelledby="docs-pipeline">
        <h2 id="docs-pipeline">How a task becomes context</h2>
        <ol className="docs-pipeline">
          <li><strong>Chunk</strong><span>Bridged text is split into 2,400-character chunks with 240-character overlap and offset-based IDs.</span></li>
          <li><strong>Rank</strong><span>Lexical scoring is combined with README/PRD/architecture priorities, exact title phrases, and definitional-query intent.</span></li>
          <li><strong>Filter</strong><span>A 35% relative score floor stops empty <code>top_k</code> slots from being filled with low-relevance documents; explicit relationships are exempt.</span></li>
          <li><strong>Pack</strong><span>Parent-path diversity comes first, then remaining chunks, under <code>top_k</code>, token, and character budgets.</span></li>
          <li><strong>Return</strong><span>Every item carries evidence id, path, title, score, chunk offsets, and token counts.</span></li>
        </ol>
      </section>

      <section className="public-section" aria-labelledby="docs-entry-points">
        <h2 id="docs-entry-points">Three agent entry points</h2>
        <ResponsiveTable label="Evidence Layer agent entry points">
          <table className="public-table public-table--compact">
            <caption>All three paths use the same workspace-scoped evidence and provenance model.</caption>
            <thead><tr><th scope="col">Path</th><th scope="col">Who uses it</th><th scope="col">What it returns</th></tr></thead>
            <tbody>
              <tr><th scope="row">Workspace task-context</th><td>A signed-in person preparing a task</td><td>Task fields (task, expected outcome, context requirements), selected evidence, budgets, relationships.</td></tr>
              <tr><th scope="row">Personal API-key retrieve</th><td>An agent or backend with a workspace-scoped read-only key</td><td>Selected chunks plus path, score, offsets, and the full parent document on request.</td></tr>
              <tr><th scope="row">Remote MCP</th><td>An MCP client such as Kilo, with a hosted URL and personal key</td><td><code>retrieve_evidence</code> and <code>get_document</code>; no local Python required.</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
      </section>

      <section className="public-section" aria-labelledby="docs-status">
        <h2 id="docs-status">What is live today</h2>
        <div className="public-card-grid">
          <article className="public-card">
            <h3>Live and production-verified</h3>
            <ul>
              <li>Workspace task-context with bounded selection and relationship expansion.</li>
              <li>Personal API-key retrieve/get-document and hosted Remote MCP.</li>
              <li>Chunked retrieval, README/PRD/architecture prioritisation, relevance floor, and provenance metadata.</li>
              <li>GitHub App and Notion personal connector slices; GitHub sync bridge and source catalogue.</li>
              <li>Supabase Auth, RLS-backed personal/organization workspaces, exports, account deletion, and the deletion worker.</li>
              <li>Request-opt-in JEV production scoring, with whole-batch lexical fallback on failure.</li>
              <li>Content-free server task ledger for retrieval latency, token, selection, and scoring metrics.</li>
            </ul>
          </article>
          <article className="public-card public-card--muted">
            <h3>Partial or constrained</h3>
            <ul>
              <li>JEV is primary when the server flag is enabled; set <code>use_jev=false</code> to force the lexical fallback.</li>
              <li>Google Drive remains <strong>Coming soon</strong> pending OAuth verification; Notion still needs a live production smoke.</li>
              <li>Export is narrowed to the personal/account path; shared/group export and general CDN delivery are unavailable.</li>
              <li>Background connector scheduling, ACL propagation, source verification, and deletion propagation are not complete.</li>
              <li>Telemetry combines a browser-local view with a content-free server ledger for retrieval metrics; agent cost and answer quality are not collected yet.</li>
              <li>Backups are logical dumps with no point-in-time recovery; Auth, Storage, and extensions are outside the dump, and a restore can reintroduce deleted rows until deletion reconciliation completes.</li>
            </ul>
          </article>
          <article className="public-card public-card--muted">
            <h3>Not implemented</h3>
            <ul>
              <li>Agent execution and final-answer generation inside Evidence Layer.</li>
              <li>Agent token/cost/quality measurement and independent quality scoring; the server ledger stores retrieval metrics only.</li>
              <li>Vector database, embeddings, or ANN indexing; selection is deterministic lexical.</li>
              <li>Billing, usage metering, free-tier limits, and a customer Comparison Lab.</li>
              <li>A source ingestion/publish UI with background scheduling.</li>
            </ul>
          </article>
        </div>
      </section>

      <section className="public-section" aria-labelledby="docs-boundary">
        <h2 id="docs-boundary">Security, provenance, and what EL does not do</h2>
        <ul>
          <li><strong>Workspace identity:</strong> Supabase Auth, cookie-backed sessions, MFA, lifecycle gates, and workspace membership.</li>
          <li><strong>Tenant boundary:</strong> RLS-backed evidence and personal API keys scoped to one workspace; no cross-tenant fallback.</li>
          <li><strong>Provenance:</strong> selected items retain evidence id, path, title, chunk offsets, score, and token counts.</li>
          <li><strong>No answer authority:</strong> the caller&apos;s model decides how to answer; Evidence Layer does not certify truth or citations.</li>
          <li><strong>No source grant:</strong> a retrieval result does not grant source access or permission to transfer text to another provider.</li>
          <li><strong>No hidden provider use:</strong> model provider keys are for evaluation scripts; the hosted service does not call OpenAI or Anthropic for user answers.</li>
        </ul>
      </section>

      <section className="public-section" aria-labelledby="docs-value">
        <h2 id="docs-value">Measured value</h2>
        <p>
          The current 15-question production run reduced context tokens by <strong>80.9%</strong> with
          DeepSeek, <strong>80.7%</strong> with OpenAI, and <strong>80.5%</strong> with Anthropic. The
          49-document GitHub-synced chunked run reduced tokens by <strong>80.7%</strong>. These
          figures are token reduction measured on 15 questions from one repository; answer quality
          was not established in general. A 50-question quality gate (2026-11-27) found answer
          quality below a full-file baseline; retrieval improvements are in progress.
        </p>
        <p className="public-report-links">
          <Link href="/evaluation">Read Evaluation · Benchmarks</Link>
          <Link href="/cost-performance">Estimate cost and latency</Link>
        </p>
      </section>

      <section className="public-callout" aria-labelledby="docs-start">
        <p className="public-eyebrow">Start here</p>
        <h2 id="docs-start">Use the workspace, the API, or the MCP</h2>
        <p>
          Create a workspace and personal API key for agent access. Use the hosted MCP URL for a
          remote client, or call the personal API-key retrieve endpoint directly. The workspace
          task-context path is for preparing a task before an agent runs.
        </p>
        <p className="public-report-links">
          <Link href="/workspace">Open the workspace</Link>
          <Link href="/workspace#agent-connection">Connect an agent</Link>
          <Link href="/cost-performance">Plan tokens and cost</Link>
        </p>
      </section>
    </PublicSiteShell>
  );
}
