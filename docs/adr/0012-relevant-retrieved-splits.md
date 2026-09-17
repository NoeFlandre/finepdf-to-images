# ADR-0012 — Derived relevant and retrieved splits for documents

Status: accepted (2026-09-17)

## Context

The published `documents` table contained all scored documents from the sampled shard (1,000 documents in the pilot run), including those judged non-relevant (`relevant == false`) or never retrieved (`retrieved == false`).

While publishing every scored row is critical for auditing and reproducibility (so readers can see false negatives and retrieval failure reasons), downstream consumers commonly want only the relevant documents (e.g. for downstream NLP tasks, agricultural domain modeling, or image-text alignment) or only the retrieved documents. Previously, users loading the dataset via `load_dataset("NoeFlandre/finepdf-to-images-poc", "documents")` had to load the entire 1,000-document table and filter client-side.

Hugging Face dataset configurations natively support multiple named splits under each config.

## Decision

1. **Pure domain derivation:** Compute `derive_relevant_rows(documents)` (where `row["relevant"] is True`) and `derive_retrieved_rows(documents)` (where `row["retrieved"] is True`) within `domain/publication.py`. No additional I/O or external dependencies are introduced.
2. **Export split JSONL files:** Generate `data/documents_relevant.jsonl` and `data/documents_retrieved.jsonl` alongside `data/documents.jsonl` during publication planning (`build_plan`).
3. **Card front matter splits:** Declare three splits under the `documents` configuration in the dataset card front matter:
   - `all`: `data/documents.jsonl`
   - `relevant`: `data/documents_relevant.jsonl`
   - `retrieved`: `data/documents_retrieved.jsonl`
   The `images` configuration remains mapped to split `train` (`data/images.jsonl`).
4. **Manifest and card transparency:** Record split file names, row counts, and content digests in `manifest.json` under `"splits"`, and render a `## Splits` documentation table in the generated dataset card (`README.md`).

## Consequences

- Downstream users can directly load desired subsets with `load_dataset("NoeFlandre/finepdf-to-images-poc", "documents", split="relevant")` or view them independently in the Hugging Face Dataset Viewer.
- Auditability is preserved: `all` retains all negative, refused, and unretrieved rows.
- Canonical JSONL sorting and source shard ordering are preserved across all split files.
- The pipeline architecture remains hexagonal and pure: `run_publish` requires no adapter churn as it publishes all files defined in `PublicationPlan`.
