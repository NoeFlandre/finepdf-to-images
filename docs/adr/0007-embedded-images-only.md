# ADR-0007: Extract embedded images, and nothing else

Status: accepted (2026-09-16)

## Context

"Publish the images in agriculture-relevant PDFs" has at least three meanings:

- the pictures that are embedded in the file
- the pages that a renderer renders as images
- the figures that a layout analysis identifies

They need very different machinery. The first is a parse. The second is a renderer. The third is a model.

## Decision

Extract the **embedded** image XObjects with one pinned library (`pypdf`). Do nothing else. Do not render pages. Do not run OCR. Do not infer layout. Do not classify images.

The dimensions come from the decoded image. They do not come from the `/Width` and `/Height` that the PDF declares. Check the bytes against the magic bytes of the media type that the extractor claimed. Content-address the images and deduplicate them across the run.

## Consequences

- A scanned document gives one image for each page. This is the literal truth about its contents. A reader who looks for "figures" does not want it. The documentation states it. It does not hide it.
- A document with vector drawings as figures gives nothing. This is a legitimate result with zero images.
- `pypdf` and `Pillow` become runtime dependencies. Pillow gives the real dimensions and format. It does not use the claims of the document. Both libraries are on the list of imports that the domain must not use. Thus they stay in the adapter.
- The extraction output depends on the decoding of these two libraries. Thus the project pins both to **exact** versions and not to minimum versions. The tests contain the expected image hashes. The hand-written PDF fixtures alone were not enough. They store raw samples that Pillow re-encodes. Without pinned hashes, a Pillow upgrade silently changes each published artifact.
- To add rendering or OCR later is a new adapter behind the same `ImageExtractor` protocol. It is not a rewrite.
