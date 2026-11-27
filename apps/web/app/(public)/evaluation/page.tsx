import type { Metadata } from "next";
import { PublicSiteShell, ResponsiveTable } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Evaluation · Benchmarks | Evidence Layer",
  description: "Evidence Layer benchmark results: measured context reduction with its conditions, and a 50-question answer-quality gate.",
};

const phase7Report = "/evaluation/phase7-report.html";
const phase7Manifest = "/evaluation/phase7-manifest.html";
const haikuLexicalReport = "/evaluation/haiku-lexical-report.html";

export default function EvaluationBenchmarksPage() {
  return (
    <PublicSiteShell
      currentPath="/evaluation"
      eyebrow="Evaluation · Benchmarks"
      title="Evaluation · Benchmarks"
      description="Two questions: how much less context does Evidence Layer send, and what happens to answer quality? These results keep the measured reduction, its conditions, the quality findings, and the limits together."
    >
      <section className="public-callout" aria-labelledby="value-claim">
        <p className="public-eyebrow">The value test</p>
        <h2 id="value-claim">Less context. Same task. Measured both ways.</h2>
        <p>
          Evidence Layer receives a task and a pool of candidate evidence, selects a bounded set,
          and returns provenance for the agent. The benchmark compares the same questions with the
          broad baseline context and with the selected context. The 15-question runs measured token
          reduction; they did not establish that answer quality is preserved in general.
        </p>
      </section>

      <section className="public-callout" aria-labelledby="quality-gate-title">
        <p className="public-eyebrow">Quality gate · 2026-11-27</p>
        <h2 id="quality-gate-title">Answer quality is below a full-file baseline today</h2>
        <p>
          A 50-question, blind-judged quality gate (2026-11-27) found Evidence Layer&apos;s answer
          quality below a full-file baseline (the three best-matching files) and statistically tied
          with classic retrieval. The main cause is retrieval recall. Retrieval improvements are in
          progress.
        </p>
      </section>

      <section className="public-section" aria-labelledby="headline-title">
        <h2 id="headline-title">Headline results</h2>
        <p>
          The strongest evidence is the 2026-11-14 multi-provider run and the 2026-11-13 chunked
          GitHub-synced run. Both use the production retrieval path.
        </p>
        <ResponsiveTable label="Headline Evidence Layer A/B results">
          <table className="public-table public-table--compact">
            <caption>Context tokens without EL → with EL, measured on 15 questions from one repository. Token reduction is measured; answer quality is a manual sufficiency readout on these questions only and is not established in general.</caption>
            <thead>
              <tr>
                <th scope="col">Experiment</th>
                <th scope="col">Without EL</th>
                <th scope="col">With EL</th>
                <th scope="col">Reduction</th>
                <th scope="col">Quality</th>
              </tr>
            </thead>
            <tbody>
              <tr><th scope="row">15 questions · DeepSeek</th><td>18,954 tokens</td><td>3,626 tokens</td><td><strong>80.9%</strong></td><td>3 wins · 11 ties · 1 loss</td></tr>
              <tr><th scope="row">15 questions · OpenAI</th><td>18,283 tokens</td><td>3,523 tokens</td><td><strong>80.7%</strong></td><td>3 wins · 11 ties · 1 loss</td></tr>
              <tr><th scope="row">15 questions · Anthropic</th><td>29,712 tokens</td><td>5,793 tokens</td><td><strong>80.5%</strong></td><td>4 wins · 11 ties · 0 losses</td></tr>
              <tr><th scope="row">49 GitHub documents · chunked retrieve</th><td>18,944 tokens</td><td>3,651 tokens</td><td><strong>80.7%</strong></td><td>3 wins · 11 ties · 1 loss</td></tr>
              <tr><th scope="row">40 cases · Phase 7 OpenAI</th><td>507.75 tokens/case</td><td>386.10 tokens/case</td><td><strong>24.0%</strong></td><td>No general quality gain established</td></tr>
              <tr><th scope="row">40 cases · Phase 7 DeepSeek</th><td>728.95 tokens/case</td><td>570.52 tokens/case</td><td><strong>21.7%</strong></td><td>No general quality gain established</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">
          Every one of the 15 questions used fewer tokens with EL for all three providers. The
          Phase 7 rows are older, smaller-context estimates; their cost and latency values are not
          pooled with the 15-question runs.
        </p>
      </section>

      <section className="public-section" aria-labelledby="mechanism-title">
        <h2 id="mechanism-title">What EL actually changes</h2>
        <ResponsiveTable label="Evidence Layer retrieval mechanics">
          <table className="public-table public-table--compact">
            <caption>Production retrieval settings behind the results</caption>
            <thead><tr><th scope="col">Stage</th><th scope="col">Behaviour</th></tr></thead>
            <tbody>
              <tr><th scope="row">Chunking</th><td>2,400-character target, 240-character overlap, max 48 chunks per evidence; stable <code>evidence_id:start:end</code> identity.</td></tr>
              <tr><th scope="row">Ranking</th><td>Lexical scoring with README/PRD/architecture/documentation boosts, exact title-phrase matching, and definitional-query intent handling.</td></tr>
              <tr><th scope="row">Relevance floor</th><td>After the first useful chunk, candidates below 35% of the highest selected score do not fill empty slots. Explicit related evidence is exempt.</td></tr>
              <tr><th scope="row">Packing</th><td>Parent-path diversity first, then remaining chunks, under <code>top_k</code>, token, and character budgets.</td></tr>
              <tr><th scope="row">Provenance</th><td>Every selected item returns evidence id, path, title, score, chunk offsets, and token counts.</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
      </section>

      <section className="public-section" aria-labelledby="quality-title">
        <h2 id="quality-title">Quality readout on the 15 questions</h2>
        <p>
          The benchmark does not treat a smaller context as success by itself. The manual readout
          checks whether the answer is sufficient, partial, or insufficient against the question,
          on these 15 questions only; the larger 2026-11-27 quality gate above found answer
          quality below a full-file baseline.
          The three anchor questions below remain the first check: product, architecture, and the
          deletion worker.
        </p>
        <ResponsiveTable label="Anchor-question quality by provider">
          <table className="public-table public-table--compact">
            <caption>Q1 product · Q2 architecture · Q13 deletion worker</caption>
            <thead><tr><th scope="col">Provider</th><th scope="col">Q1</th><th scope="col">Q2</th><th scope="col">Q13</th><th scope="col">Overall</th></tr></thead>
            <tbody>
              <tr><th scope="row">DeepSeek</th><td>Sufficient</td><td>Sufficient</td><td>Sufficient</td><td>3 wins · 11 ties · 1 loss</td></tr>
              <tr><th scope="row">OpenAI</th><td>Sufficient</td><td>Sufficient</td><td>Sufficient</td><td>3 wins · 11 ties · 1 loss</td></tr>
              <tr><th scope="row">Anthropic</th><td>Sufficient</td><td>Sufficient</td><td>Sufficient</td><td>4 wins · 11 ties · 0 losses</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">
          These are manual sufficiency labels, not an independent human preference study.
          “Insufficient context” is the hard failure signal; wins and losses are based on that
          signal. A cross-model automated judge was tested but was not reliable enough to use as
          the primary quality verdict.
        </p>
      </section>

      <section className="public-section" aria-labelledby="limits-title">
        <h2 id="limits-title">What the results do not prove</h2>
        <ul>
          <li>The baseline is capped at 80,000 characters; this is not a full-corpus baseline.</li>
          <li>The 15-question set is small and not a sealed confirmatory set. The exposed Real-World v1 split is not a blind test.</li>
          <li>Quality is checked for sufficiency, not by independent human reviewers or citation-level factuality.</li>
          <li>OpenAI and Anthropic use their native generation settings because the pinned models reject temperature <code>0.2</code>; DeepSeek uses <code>0.2</code>.</li>
          <li>Token counts from EL are estimates; provider usage is authoritative for generation tokens.</li>
          <li>No JEV call is part of the headline multi-provider run. JEV remains an optional scorer and is not required for the measured token reduction.</li>
        </ul>
      </section>

      <section className="public-callout" aria-labelledby="next-title">
        <p className="public-eyebrow">Next validation step</p>
        <h2 id="next-title">From retrospective evidence to a blinded comparison</h2>
        <p>
          The next step is a blinded, side-by-side human comparison on the existing answer pairs,
          followed by a new sealed set for confirmatory claims. The product does not claim
          independent superiority before that review is complete.
        </p>
        <p className="public-report-links">
          <a href="/cost-performance">Estimate tokens, API calls, cost, and latency</a>
          <a href={phase7Report}>Read the Phase 7 report</a>
          <a href={phase7Manifest}>View the Phase 7 manifest</a>
          <a href={haikuLexicalReport}>Read the Haiku lexical report</a>
        </p>
      </section>
    </PublicSiteShell>
  );
}
