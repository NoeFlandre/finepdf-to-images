# Extracting images

```bash
uv run finepdf-to-images extract \
  --retrieved out/retrieve/retrieved.jsonl \
  --pdf-root out/retrieve \
  --out out/extract
```

## Scope, deliberately

This extracts images **embedded** in a PDF. It does **not**:

- render pages to images,
- run OCR,
- infer layout or classify what a picture shows.

A scanned document whose every page is one big image produces one image per page. That is correct,
and it is not the same thing as "the figures in this document". Both limits are out of scope for
this first POC — adding them without a fixture demonstrating they are needed would be guessing.

## What each image record carries

`document_row_id`, `document_row_index`, `pdf_sha256`, `page_index`, `image_index`, `sha256`,
`mime`, `width`, `height`, `byte_size`, `path`, `duplicate_of`.

Page and image indices are **positions, not identity**: the same picture can appear on several
pages, and each occurrence gets its own record pointing at one shared artifact.

Dimensions come from the **decoded image**, not from the PDF's `/Width` and `/Height` entries.
Those are what the document claims; a manifest should record what the artifact actually is.

## Input safety

The stored path must be **exactly** the content-addressed path the digest implies, and the bytes
read must hash back to that digest. Without the first check a hand-edited `retrieved.jsonl` naming
`../secret.pdf` — or an absolute path, which `pathlib` resolves by discarding the root entirely —
would read arbitrary files and publish the images inside them. The second catches a corrupted or
swapped artifact rather than indexing it under a false identity.

`max_images` is counted from the page resource dictionaries **before any image is decoded**.
Checking it while collecting meant pypdf had already decoded a whole page by the time the limit
fired: a 395 KB document declaring 300 images at 600×600 peaked at **283 MB** of resident memory
before refusing. It now peaks at 13 MB.

## Identity and layout

`images/<aa>/<bb>/<sha256>.<ext>`, sharded so no directory grows without bound. Deduplication is by
content across the **whole run**, so the same logo on forty pages is one artifact with forty
references.

Supported media types are `image/png`, `image/jpeg` and `image/tiff`. The bytes are checked against
the magic bytes for the type the extractor claimed — a library's label is a claim, the bytes are the
evidence, the same reasoning as the PDF header check in [retrieval](retrieval.md).

## Ordering

Document, then page, then position on the page. All three come from the PDF itself, so this is the
document's own order rather than an arbitrary one, and two runs over the same input produce the
same manifest bytes.

Which occurrence of a repeated image is recorded as the original depends on the order of the input
records. The artifact is content-addressed, so only the `duplicate_of` pointer moves — and it
references `<pdf_sha256>#<page>.<index>`, not a row id, because a row id can be empty or repeated
across documents.

## Failures

A **PDF with no embedded images is a zero-image success**, not a failure — most PDFs on the open
web genuinely contain none.

A document that cannot be read fails with a bounded diagnostic and contributes **nothing**. Partial
output from a document we could not read would be worse than none. One failure never stops the
others.

Real reasons seen on the pinned shard:

- `DependencyError: jbig2dec binary is not available` — an optional external decoder this pilot has
  no reason to install.
- `document declares more than 200 images; refusing to unpack` — a bound, not a preference.

## Real-data results

Over the 16 PDFs retrieved from the pinned shard:

| | |
| --- | --- |
| documents | 16 |
| with images | 10 |
| failed | 2 |
| image references | 71 |
| unique artifacts | 62 |
| output size | 2.7 MB |
| types | 52 JPEG, 19 PNG |
| sizes | 2×50 to 1241×1755 |

## Fixtures

`tests/fixtures/pdfs/` holds seven PDFs **written by hand, byte by byte**, in
`tests/fixtures/build_pdf_fixtures.py`. They are golden fixtures: a PDF library that changed its
output between versions would change the expected hashes, and then the test would be asserting the
library's behaviour rather than ours. Each is a few hundred bytes and readable in a text editor.

Cases: two images on a page, the same image twice, no images at all, a rotated page, two pages,
malformed bytes, and a truncated file.

```bash
uv run python tests/fixtures/build_pdf_fixtures.py
```

### Pinned hashes

The fixtures store raw `FlateDecode` samples, and **pypdf and Pillow re-encode them to PNG** — so
the published artifact's bytes, its `sha256`, its content-addressed path and the manifest digest are
all a function of those two libraries. They are therefore pinned to exact versions in
`pyproject.toml`, and the expected hashes are written down in `GOLDEN_IMAGES`.

Without that, a Pillow bump would silently change every image in the dataset and no test would
notice. If one of those hashes fails after a dependency bump, the test is working; re-pin
deliberately.
