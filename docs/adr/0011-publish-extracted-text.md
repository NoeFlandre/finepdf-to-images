# ADR-0011 — Publish extracted document text under ODC-BY

Status: accepted (2026-09-17)

## Context

The pipeline previously extracted document text from FinePDFs, scored it for agriculture concepts,
and then dropped the text. The published document table retained `relevance_score` and `matched_terms`
but omitted the source text.

This prevented auditing: a reader could see that `irrigation` or `soil` had matched, but could not
inspect the sentence or context without re-downloading the raw FinePDFs parquet shard.

Unlike third-party PDF binaries or embedded image bytes (which originate from arbitrary web crawl hosts),
the extracted text is part of the collection content of FinePDFs itself, which is licensed under ODC-BY.
Redistributing this text is legally defensible provided that the ODC-BY attribution requirement is met.

## Decision

1. **Carry text and digest:** Retain `text` and compute `text_sha256` during the scoring stage,
   carrying both through to the published `documents` table.
2. **Prevent text drift:** Verify that any recorded `text_sha256` matches `sha256_hex(text.encode("utf-8"))`
   when assembling published document rows (`build_document_rows` and `_document_row`). A mismatch
   raises `PublicationError`.
3. **Enforce hard byte cap in domain:** Enforce a hard ceiling on total published text bytes across all
   documents (`MAX_DOCUMENT_TEXT_BYTES = 50 * 1024 * 1024`, 50 MB), counted across every file the
   plan publishes — the derived splits of ADR-0012 republish the same text. The 1000-row pilot
   yields ~30 MB across the three document files;
   the cap prevents unbounded text dumps at upload time.
4. **Attribution obligation:** Explicitly state the ODC-BY attribution obligation in `SOURCE_ATTRIBUTION`
   and the generated dataset card (`README.md`).
5. **Schema version:** Bump `SCHEMA_VERSION` from 1 to 2.

## Consequences

- The scorer's decisions and `matched_terms` are auditable directly from the published dataset.
- The document table payload size increases. The 1000-row pilot publishes ~30 MB of text across
  the three document files of ADR-0012, remaining within the 50 MB cap. The ~24 MB figure this
  line carried before counted the `all` file alone, which is the under-count that let the cap
  be exceeded; see ADR-0012.
- Schema version 2 distinguishes datasets carrying extracted text from legacy schema version 1.
