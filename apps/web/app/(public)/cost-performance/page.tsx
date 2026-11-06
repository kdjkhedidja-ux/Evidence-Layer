import type { Metadata } from "next";
import { PublicSiteShell, ResponsiveTable } from "../../public-site-shell";

export const metadata: Metadata = {
  title: "Cost & Performance | Evidence Layer",
  description: "Separate results for Phase 6 ranking, Phase 7 answer generation, and Phase 7B live JEV end-to-end cost validation.",
};

const phase6Report = "/evaluation/phase6-report.html";
const phase7Report = "/evaluation/phase7-report.html";
const phase7Manifest = "/evaluation/phase7-manifest.html";
const phase7bReport = "/evaluation/phase7b-report.html";
const phase7bManifest = "/evaluation/phase7b-manifest.html";
const haikuLexicalReport = "/evaluation/haiku-lexical-report.html";

export default function CostPerformancePage() {
  return (
    <PublicSiteShell
      currentPath="/cost-performance"
      description="Three distinct experiments answer different questions. Their results are shown separately; estimates and limitations are kept with the measurements."
      eyebrow="Evaluation · Benchmarks"
      title="Cost & Performance"
    >
      <section className="public-callout" aria-labelledby="benchmark-scope">
        <p className="public-eyebrow">Shared evaluation context</p>
        <h2 id="benchmark-scope">Real-World v1 · fixed inputs</h2>
        <p>
          The experiments use 51 passages, 40 evaluation cases, and 200 candidate records. The
          relevance judgments were written by the developer and have not received independent human
          review. The 8-case exposed test split was disclosed previously; it is not a blind or
          independent scientific validation set. The frozen synthetic benchmark and historical
          result files are separate and unchanged.
        </p>
      </section>

      <section className="public-section benchmark-section" aria-labelledby="phase6-title">
        <div className="benchmark-heading">
          <div><p className="public-eyebrow">Phase 6 · Evidence ranking</p><h2 id="phase6-title">Real-World Benchmark</h2></div>
          <span className="benchmark-tag">40 cases · 200 candidates</span>
        </div>
        <p>
          JEV-ranked evidence was compared with the frozen Lexical baseline. This phase measures
          candidate ordering, not generated answer quality or total service cost.
        </p>
        <ResponsiveTable label="Phase 6 Real-World JEV and lexical results">
          <table className="public-table public-table--compact">
            <caption>Phase 6 overall ranking metrics, macro-averaged over 40 cases</caption>
            <thead><tr><th scope="col">Metric</th><th scope="col">JEV</th><th scope="col">Lexical</th><th scope="col">Difference</th></tr></thead>
            <tbody>
              <tr><th scope="row">MAP</th><td>0.943</td><td>0.777</td><td>+0.166</td></tr>
              <tr><th scope="row">MRR</th><td>0.967</td><td>0.795</td><td>+0.172</td></tr>
              <tr><th scope="row">Precision@1</th><td>0.950</td><td>0.650</td><td>+0.300</td></tr>
              <tr><th scope="row">Recall@5</th><td>1.000</td><td>1.000</td><td>0.000</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">Descriptive performance against developer-authored labels; the exposed split is not blind and the result does not establish general superiority.</p>
        <p className="public-report-links"><a href={phase6Report}>Read the Phase 6 report</a></p>
      </section>

      <section className="public-section benchmark-section" aria-labelledby="phase7-title">
        <div className="benchmark-heading">
          <div><p className="public-eyebrow">Phase 7 · Answer-generation comparison</p><h2 id="phase7-title">End-to-End RAG Benchmark</h2></div>
          <span className="benchmark-tag">Historical JEV results reused</span>
        </div>
        <p>
          Traditional RAG passed the top five lexical candidates. Evidence Layer passed the top
          three candidates selected using saved Phase 6 JEV scores. The intended route difference
          was evidence selection and context size. Phase 7 made no new JEV calls; JEV API cost was
          unavailable and total system cost cannot be determined from this phase. A separate paired
          Haiku run selects with the lexical scorer only and makes no JEV call at all; it appears
          below next to the JEV-estimated Anthropic rows.
        </p>
        <ResponsiveTable label="Phase 7 answer-generation results by provider">
          <table className="public-table">
            <caption>Phase 7 mean per answer; cost is LLM-only estimate and does not include JEV</caption>
            <thead><tr><th scope="col">Provider</th><th scope="col">Total tokens<br />Traditional → EL</th><th scope="col">LLM cost<br />Traditional → EL</th><th scope="col">E2E latency<br />Traditional → EL</th><th scope="col">Evidence coverage</th><th scope="col">Quality readout</th></tr></thead>
            <tbody>
              <tr><th scope="row">OpenAI</th><td>507.75 → 386.10<br /><strong>−23.96%</strong></td><td>$0.001741 → $0.001472<br /><strong>−15.48%</strong></td><td>2,264 → 3,061 ms<br /><strong>+796 ms estimated</strong></td><td>1.000 → 0.979</td><td>Correctness unchanged; completeness lower; no general improvement established</td></tr>
              <tr><th scope="row">DeepSeek</th><td>728.95 → 570.52<br /><strong>−21.73%</strong></td><td>$0.000823 → $0.000716<br /><strong>−13.05%</strong></td><td>4,974 → 5,094 ms<br /><strong>+121 ms estimated</strong></td><td>1.000 → 0.979</td><td>Small rubric changes vary by metric; no general improvement established</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">
          Phase 7 costs are LLM usage estimates only; three earlier incomplete DeepSeek responses
          lacked usage fields. JEV charges were unknown. Latency adds historical Phase 6 JEV timing
          and is not a fresh live end-to-end measurement. Automated answer judgments are not
          independent human review.
        </p>
        <h3>Anthropic — Evidence Layer avec et sans JEV</h3>
        <ResponsiveTable label="Phase 7 Anthropic results, with and without JEV">
          <table className="public-table">
            <caption>Claude 5.5 · Traditional RAG → Evidence Layer · coûts par bras de 40 cas</caption>
            <thead><tr><th scope="col">Modèle et variante</th><th scope="col">Tokens / cas</th><th scope="col">Coût LLM mesuré</th><th scope="col">Coût système</th><th scope="col">Lecture qualité</th></tr></thead>
            <tbody>
              <tr>
                <th scope="row">Haiku · EL + lexical<br /><span className="benchmark-tag">Sans JEV</span></th>
                <td>912.25 → 701.00<br /><strong>−23.16 %</strong></td>
                <td>$0.007876 → $0.006698<br /><strong>−14.96 %</strong></td>
                <td>$0.007876 → $0.006698<br /><strong>−14.96 %</strong></td>
                <td>Correctness 2.000 → 1.975 ; complétude 1.950 → 1.725 (IC −0.40 ; −0.075) ; groundedness 1.975 → 2.000. Aucun gain de qualité établi.</td>
              </tr>
              <tr>
                <th scope="row">Haiku · EL + JEV estimé</th>
                <td>906.15 → 710.43<br /><strong>−21.60 %</strong></td>
                <td>$0.007754 → $0.006765</td>
                <td>$0.007754 → $0.009965<br /><strong>+$0.002210</strong></td>
                <td>Aucun gain de qualité établi.</td>
              </tr>
              <tr>
                <th scope="row">Sonnet · EL + JEV estimé</th>
                <td>822.75 → 631.68<br /><strong>−23.22 %</strong></td>
                <td>$0.121724 → $0.103798</td>
                <td>$0.121724 → $0.106998</td>
                <td>Aucun gain de qualité établi.</td>
              </tr>
              <tr>
                <th scope="row">Opus · EL + JEV estimé</th>
                <td>915.75 → 723.00<br /><strong>−21.05 %</strong></td>
                <td>$0.317848 → $0.280656</td>
                <td>$0.317848 → $0.283856</td>
                <td>Aucun gain de qualité établi.</td>
              </tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">
          La variante « sans JEV » n’a fait aucun appel JEV : coût système = coût LLM mesuré. Elle
          corrige le surcoût Haiku observé avec l’estimation JEV, mais la complétude baisse de 0.225
          et le rappel des passages or de 1.000 à 0.871. Les lignes « JEV estimé » utilisent un prix
          liste, pas une facture.
        </p>
        <p className="public-report-links"><a href={haikuLexicalReport}>Read the Haiku lexical-only report</a></p>
        <p className="public-report-links"><a href={phase7Report}>Read the Phase 7 report</a><a href={phase7Manifest}>View the Phase 7 manifest</a></p>
      </section>

      <section className="public-section benchmark-section" aria-labelledby="phase7b-title">
        <div className="benchmark-heading">
          <div><p className="public-eyebrow">Phase 7B · Live cost validation</p><h2 id="phase7b-title">Fresh JEV in the end-to-end path</h2></div>
          <span className="benchmark-tag">40 cases · 160 answers · 400 live JEV scores</span>
        </div>
        <p>
          Phase 7B reran all four provider/route arms and made fresh JEV scoring requests within
          each Evidence Layer arm. The 80 paired automated answer judgments are exploratory. Values
          below compare the same fixed 40 cases; system cost is estimated from provider usage and
          published rates, not from account invoices.
        </p>
        <ResponsiveTable label="Phase 7B live cost validation by provider">
          <table className="public-table">
            <caption>Phase 7B overall results; Traditional RAG → Evidence Layer</caption>
            <thead><tr><th scope="col">Measure</th><th scope="col">OpenAI</th><th scope="col">DeepSeek</th></tr></thead>
            <tbody>
              <tr><th scope="row">Mean total tokens per answer</th><td>510.35 → 384.05<br /><strong>−24.75%</strong></td><td>798.125 → 567.25<br /><strong>−28.93%</strong></td></tr>
              <tr><th scope="row">Estimated total API cost per 40-case arm</th><td>$0.070700 → $0.061188<br /><strong>−13.45%</strong></td><td>$0.029040 → $0.025995<br /><strong>−10.49%</strong></td></tr>
              <tr><th scope="row">Mean end-to-end latency</th><td>2,329 → 3,005 ms<br /><strong>+676 ms</strong></td><td>4,144 → 4,159 ms<br /><strong>+16 ms</strong></td></tr>
              <tr><th scope="row">Evidence coverage</th><td>1.0000 → 0.9792</td><td>1.0000 → 0.9792</td></tr>
              <tr><th scope="row">Mean correctness score / 2</th><td>2.00 → 2.00</td><td>1.95 → 2.00</td></tr>
              <tr><th scope="row">Paired total-cost interval</th><td>Reduction interval excludes zero</td><td>Includes zero; reduction is not statistically conclusive on this fixed set</td></tr>
            </tbody>
          </table>
        </ResponsiveTable>
        <p className="public-caveat">
          No general answer-quality improvement was established. Coverage decreased by 0.0208 in
          both providers. OpenAI’s estimated system-cost reduction is supported by the paired
          interval for this dataset; the DeepSeek interval includes zero. OpenAI mean latency rose
          by about 676 ms; DeepSeek’s 16 ms increase is not conclusive. Estimated costs are not
          invoices and do not prove future savings.
        </p>
        <p className="public-report-links"><a href={phase7bReport}>Read the Phase 7B report</a><a href={phase7bManifest}>View the Phase 7B manifest</a></p>
      </section>

      <section className="public-section" aria-labelledby="benchmark-method">
        <h2 id="benchmark-method">Method and limits</h2>
        <ul>
          <li>All three phases used the fixed Real-World v1 questions and developer-authored relevance labels. Those labels have not been independently human-reviewed.</li>
          <li>The eight-case exposed test split was disclosed earlier and is not a blind test. It should not be used as blind evidence for future comparisons.</li>
          <li>Phase 7 and Phase 7B used an automated OpenAI judge for answer rubrics. Automated scores are not independent human judgments and the judge provider overlaps with a tested generator.</li>
          <li>Phase 6 measures evidence ranking. Phase 7 measures answer generation with cached Phase 6 JEV output. Phase 7B measures live JEV plus answer generation. Their cost and latency values answer different questions and must not be pooled.</li>
          <li>The result files, manifests, source hashes, and synthetic freeze artifacts remain separate historical records. Reproduction commands and input/output fingerprints are in each linked report or manifest.</li>
        </ul>
      </section>
    </PublicSiteShell>
  );
}
