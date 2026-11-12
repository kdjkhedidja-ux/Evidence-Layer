# Retrieval token-reduction report

Generated: 2026-11-12T22:23:02.539378+00:00
Mode: local lexical selection
Corpus documents: 5; queries: 3
Baseline: all corpus documents; selected: top_k=5, max_tokens=1200, max_chars=6000
Token estimator: chars_div_4

| Query | Status | Baseline tokens | Selected tokens | Reduction | Selected docs |
|---|---:|---:|---:|---:|---:|
| lexical retrieval context for agents | ok | 95 | 20 | 78.9% | 1 |
| GitHub connector repositories | ok | 95 | 37 | 61.1% | 2 |
| workspace scoped API keys | ok | 95 | 40 | 57.9% | 2 |

Baseline chars: 372
Mean token reduction: 66.0%

This report measures selected context size, not answer quality. It does not claim semantic retrieval or JEV.
