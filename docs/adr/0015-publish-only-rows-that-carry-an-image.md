# ADR-0015 — Publish only rows that carry an image, and stop filtering images by licence

Status: accepted (2026-09-17)

## Context

After ADR-0014 and the minimal parquet layout, the published dataset held 52 rows of which
**one** carried a visible image. Ten documents had images extracted; nine of them showed an empty
`images` column because their source was not on the redistribution allow list.

So a reader opening a dataset called `finepdf-to-images` met 51 rows with no picture, and no way
to tell "this PDF had no images" from "this PDF had images you may not see".

## Decision

1. **A document with no embeddable image is not published.** The filter is applied to what
   actually embeds, so every published row is one a reader can see something in. There is no
   empty `images` column in the published table.
2. **Images are no longer filtered by licence.** Every extracted image is published.

Decision 2 is the dataset owner's, taken explicitly with the consequences stated. It is recorded
here rather than left implicit in a diff, because it reverses the posture of ADR-0013 for images.

## Consequences

- The pilot publishes 10 rows instead of 52, and 259 images instead of 190 — from 10 sources
  instead of 1.
- **Nine of those ten sources declared no licence.** They include commercial publishers, an
  academic journal and a university extension service. Their copyright remains with them; this
  dataset reproduces their images without permission, as a research proof of concept.
- The obligation that replaces the filter is disclosure and responsiveness: the card states
  plainly that most images carry no declared licence, and carries a takedown route. Every row
  publishes its `pdf_url`, so any image can be traced to its source and removed on request.
- ADR-0013 still governs **PDF** bytes. This changes the treatment of images only, and the
  minimal layout no longer republishes PDFs at all.
- Anyone reusing this dataset inherits that exposure. The card says so; it is not a licence.

## Alternatives rejected

- **Keep the licence filter and filter rows on extracted images.** Ten rows, nine still showing
  nothing — the original confusion, merely smaller.
- **Keep the licence filter and filter rows on published images.** One row. A dataset that is
  really a function of the allow list rather than of the pipeline.
- **Grow the allow list first.** The principled fix, and still open: clearing more sources would
  let the filter be strict without the dataset collapsing. It requires a per-source licensing
  judgement that had not been made, and was not a reason to keep shipping rows a reader cannot
  use.
