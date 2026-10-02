# finepdf-to-images

This project is a small, reproducible proof of concept.

The pipeline does these steps:

1. It reads **one pinned shard** of [HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs).
2. It keeps a bounded sample of rows.
3. It scores each row for agriculture relevance. It uses the extracted text of the row.
4. It retrieves only the selected source PDFs.
5. It extracts the images that are embedded in these PDFs.
6. It publishes the bounded result to [NoeFlandre/finepdf-to-images-poc](https://huggingface.co/datasets/NoeFlandre/finepdf-to-images-poc).

For the meaning of the technical terms, see the [glossary](glossary.md).

## What this project does not do

- It does **not** download, list, or copy the FinePDFs corpus.
- It does **not** render pages, run OCR, or classify images.
- It does **not** state that a retrieved artifact is free to redistribute.

A [conservative and testable policy](policy.md) controls publication. The pipeline publishes bytes only when it has positive evidence that a person recorded. The pilot has no such evidence.

## Layout

| Layer | Package | Rule |
| --- | --- | --- |
| Domain | `finepdf_to_images.domain` | Pure. No network, filesystem, PDF or Hub imports. |
| Adapters | `finepdf_to_images.adapters` | All side effects. Thin and injectable. |
| Composition | `finepdf_to_images.cli`, `finepdf_to_images.pipeline` | Connects the adapters to the domain. |

Tests enforce these rules. See `tests/architecture/`.
