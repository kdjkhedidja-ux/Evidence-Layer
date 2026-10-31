# Corpus Attribution & Licensing

The passages in `postgresql_passages.jsonl` are quoted (some lightly
normalized for length) from the official PostgreSQL documentation:

  https://www.postgresql.org/docs/current/

The PostgreSQL documentation is published under the **PostgreSQL License**, a
liberal permissive open-source license (comparable in spirit to the MIT/BSD
licenses) that permits use, copying, modification, and distribution provided
the copyright notice is retained.

Copyright notice for the quoted documentation:

  Copyright © 1996-2026 The PostgreSQL Global Development Group

This dataset is an evaluation foundation only. It does not claim any
endorsement by, or affiliation with, the PostgreSQL Global Development Group.

## Synthetic extension benchmark

The passages in `extension_passages.jsonl` are **original synthetic content
authored for this repository**. The three publishers (`opsrunbook.example`,
`streamspec.example`, `cloudtier.example`), the products (Meridian,
StreamProto, CloudTier), and every fact stated are fictional and exist only to
exercise retrieval difficulty dimensions (near-tie confusers, graded
relevance, multi-fact coverage, compound and context-dependent queries).

This corpus is a **synthetic extension benchmark**: it controls difficulty and
publisher diversity by construction, but it does not by itself evidence
retrieval quality on real-world documents. Its results are always reported
separately from the PostgreSQL-based baseline set, and a real-world document
benchmark remains future work. No content was copied from any external source.
