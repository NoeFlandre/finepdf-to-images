# ADR-0011: Publish extracted document text under ODC-BY

Status: accepted (2026-09-17)

## Context

In the past, the pipeline extracted the document text from FinePDFs and scored it for agriculture concepts. Then it dropped the text. The published document table kept `relevance_score` and `matched_terms`. It omitted the source text.

This prevented audit. A reader could see that `irrigation` or `soil` matched. But the reader could not inspect the sentence or the context. The reader had to download the raw FinePDFs Parquet shard again.

The extracted text is different from the binaries of third-party PDFs and the bytes of embedded images. These come from arbitrary web crawl hosts. The extracted text is part of the collection content of FinePDFs. FinePDFs has the ODC-BY license. It is legally defensible to redistribute this text if the project meets the ODC-BY attribution requirement.

## Decision

1. **Carry the text and the digest.** Keep `text`. Compute `text_sha256` during the scoring stage. Carry both to the published `documents` table.
2. **Prevent text drift.** When the code assembles the published document rows (`build_document_rows` and `_document_row`), verify that each recorded `text_sha256` matches `sha256_hex(text.encode("utf-8"))`. A mismatch raises `PublicationError`.
3. **Enforce a hard byte cap in the domain.** Set a hard ceiling for the total of published text bytes across all documents (`MAX_DOCUMENT_TEXT_BYTES = 50 * 1024 * 1024`, 50 MB). Count it across each file that the plan publishes. The derived splits of ADR-0012 republish the same text. The pilot of 1000 rows gives about 30 MB across the three document files. The cap prevents unbounded text dumps at upload time.
4. **State the attribution obligation.** State the ODC-BY attribution obligation in `SOURCE_ATTRIBUTION` and in the generated dataset card (`README.md`).
5. **Set the schema version.** Increase `SCHEMA_VERSION` from 1 to 2.

## Consequences

- You can audit the decisions of the scorer and `matched_terms` directly from the published dataset.
- The size of the document table payload increases. The pilot of 1000 rows publishes about 30 MB of text across the three document files of ADR-0012. This is within the 50 MB cap. This line previously said about 24 MB. That figure counted the `all` file alone. It is the undercount that allowed the cap to be exceeded. See ADR-0012.
- Schema version 2 identifies the datasets that carry extracted text. Schema version 1 identifies the legacy datasets.
