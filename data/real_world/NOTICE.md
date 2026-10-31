# Real-World Benchmark — Attribution & Licensing

The 51 passages in `passages.jsonl` come from four real, publicly licensed
documents. Every row labels its text as either `verbatim` or `paraphrase` and
contains its section, source URL, license, and license reference. Paraphrases
are not presented as quotations. This benchmark claims no endorsement by, or
affiliation with, any publisher.

## IETF RFC Editor — RFC 7519 (JSON Web Token)

  https://www.rfc-editor.org/rfc/rfc7519.txt

Reproduced under the IETF Trust Legal Provisions, which permit reproduction
of RFC text with attribution.

  Copyright © 2015 IETF Trust and the persons identified as the document
  authors. All rights reserved.

## IETF RFC Editor — RFC 7515 (JSON Web Signature)

  https://www.rfc-editor.org/rfc/rfc7515.txt

The Base64url definition is attributed to RFC 7515, Section 2, where the
encoding is described by reference to RFC 4648 Section 5. Reproduction follows
the IETF Trust Legal Provisions, with attribution.

## Supabase documentation

  https://github.com/supabase/supabase/tree/783727708403b941fe5a59300b23c1c0711bd97b/apps/docs/content

The supabase/supabase repository is published under the **Apache License 2.0**,
which permits reproduction with attribution.

  Copyright © Supabase, Inc.

## PostgreSQL documentation (Row Security Policies chapter)

  https://www.postgresql.org/docs/current/ddl-rowsecurity.html

The PostgreSQL documentation is published under the **PostgreSQL License**, a
liberal permissive license permitting use, copying, modification, and
distribution provided the copyright notice is retained.

  Copyright © 1996-2026 The PostgreSQL Global Development Group

## Separation from other benchmarks

Results from this real-world benchmark must never be merged into a single
number with the PostgreSQL baseline or the synthetic extension benchmark; the
three are reported separately because they differ in nature and evidentiary
strength. The current relevance judgments are developer-authored and have
not received independent blind review. See the review protocol in
`docs/phase6_real_world_review_protocol.md`.
