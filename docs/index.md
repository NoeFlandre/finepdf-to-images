# finepdf-to-images

A deliberately tiny, reproducible proof of concept.

It reads **one pinned shard** of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), keeps a bounded
sample of rows, scores each row for agriculture relevance from its extracted text, retrieves only
the selected source PDFs, extracts their embedded images, and publishes the bounded result to
[NoeFlandre/finepdf-to-images-poc](https://huggingface.co/datasets/NoeFlandre/finepdf-to-images-poc).

## What this is not

- It does **not** download, enumerate, or mirror the FinePDFs corpus.
- It does **not** render pages, run OCR, or classify images.
- It does **not** assert that any retrieved artifact is freely redistributable. Publication is
  governed by a conservative, testable policy; artifacts with unknown permission are excluded from
  public binary publication.

## Layout

| Layer | Package | Rule |
| --- | --- | --- |
| Domain | `finepdf_to_images.domain` | Pure. No network, filesystem, PDF or Hub imports. |
| Adapters | `finepdf_to_images.adapters` | All side effects, thin and injectable. |
| Composition | `finepdf_to_images.cli` | Wires adapters into the domain. |

These rules are executable: see `tests/architecture/`.
