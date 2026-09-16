# ADR-0007 — Extract embedded images, and nothing else

Status: accepted (2026-09-16)

## Context

"Publish the images in agriculture-relevant PDFs" has at least three readings: the pictures
embedded in the file, the pages rendered as images, or the figures identified by layout analysis.
They need very different machinery — the first is a parse, the second a renderer, the third a
model.

## Decision

Extract **embedded** image XObjects with one pinned library (`pypdf`), and stop there. No page
rendering, no OCR, no layout inference, no image classification.

Dimensions come from the decoded image rather than the PDF's declared `/Width` and `/Height`.
Bytes are checked against the magic bytes for their claimed media type. Images are content-
addressed and deduplicated across the run.

## Consequences

- A scanned document produces one image per page, which is the literal truth about its contents
  and not what a reader looking for "figures" wants. Documented rather than papered over.
- A document whose figures are vector drawings produces nothing, and is a legitimate zero-image
  result.
- `pypdf` and `Pillow` become runtime dependencies. Pillow is what gives real dimensions and format
  rather than the document's claims; both are on the domain's forbidden-import list, so they stay
  in the adapter.
- Extraction output depends on those two libraries' decoding, so the lockfile is part of the
  reproducibility story. The PDF fixtures are hand-written precisely so that a library bump shows
  up as a test failure rather than as a silently different dataset.
- Adding rendering or OCR later is a new adapter behind the same `ImageExtractor` protocol, not a
  rewrite.
