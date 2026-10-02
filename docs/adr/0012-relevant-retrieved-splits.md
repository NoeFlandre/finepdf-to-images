# ADR-0012: Derived relevant and retrieved splits for documents

Status: accepted (2026-09-17)

## Context

The published `documents` table contained all scored documents from the sampled shard (1,000 documents in the pilot run). It included the documents that the scorer judged non-relevant (`relevant == false`) and the documents that the pipeline never retrieved (`retrieved == false`).

The publication of each scored row is critical for audit and reproducibility. Readers can see the false negatives and the reasons for retrieval failure. But downstream consumers usually want only the relevant documents, or only the retrieved documents. Examples are downstream NLP tasks, agricultural domain modeling, and image-text alignment. In the past, a user loaded the dataset with `load_dataset("NoeFlandre/finepdf-to-images-poc", "documents")`. The user had to load the whole table of 1,000 documents and filter it on the client.

The dataset configs of Hugging Face support multiple named splits in each config.

## Decision

1. **Pure domain derivation.** Compute `derive_relevant_rows(documents)` (where `row["relevant"] is True`) and `derive_retrieved_rows(documents)` (where `row["retrieved"] is True`) in `domain/publication.py`. This adds no I/O and no external dependency.
2. **Export the split JSONL files.** During publication planning (`build_plan`), generate `data/documents_relevant.jsonl` and `data/documents_retrieved.jsonl` with `data/documents.jsonl`.
3. **Declare the splits in the card front matter.** Declare three splits under the `documents` config in the front matter of the dataset card:
   - `all`: `data/documents.jsonl`
   - `relevant`: `data/documents_relevant.jsonl`
   - `retrieved`: `data/documents_retrieved.jsonl`

   The `images` config stays mapped to the split `train` (`data/images.jsonl`).
4. **Make the manifest and the card transparent.** Record the split file names, the row counts, and the content digests in `manifest.json` under `"splits"`. Render a `## Splits` documentation table in the generated dataset card (`README.md`).

## Consequences

- Downstream users can load the subsets they want directly with `load_dataset("NoeFlandre/finepdf-to-images-poc", "documents", split="relevant")`. They can also view each subset separately in the Hugging Face Dataset Viewer.
- Auditability stays. `all` keeps all negative, refused, and unretrieved rows.
- The canonical JSONL order and the order of the source shard stay the same across all split files.
- **The published text payload is duplicated.** A document that is relevant and retrieved is written into all three document files. Thus the upload contains its text three times. This is an accepted cost of native split support. The alternative is splits that hold only row ids. Then `load_dataset(..., split="relevant")` is useless without a join. The text byte cap of ADR-0011 must count each published file. It must not count the `all` set alone. When the count was done once, the pilot reported 24.7 MB against 30.1 MB that the code actually uploaded. A run with many relevant rows could exceed the cap three times and still pass.
- The pipeline architecture stays hexagonal and pure. `run_publish` needs no change to the adapter, because it publishes all files that `PublicationPlan` defines.
