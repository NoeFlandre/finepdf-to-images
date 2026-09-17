# ADR-0016 — One row per image, so the viewer shows pictures

Status: accepted (2026-09-17)

## Context

ADR-0015 left the dataset as one row per document, with an `images` column holding a list of
embedded pictures. `datasets-server` typed that column exactly as intended:

```
feature images -> {"feature": {"_type": "Image"}, "_type": "List"}
```

and served every image as a real asset. The data was correct.

The **viewer** nevertheless displayed the cell as raw JSON:

```
[{"src":"https://datasets-server.huggingface.co/assets/NoeFlandre/finepdf...
```

The Hub's viewer renders a *scalar* `Image` feature as a thumbnail. It does not render a list of
them. So the headline feature of a dataset called `finepdf-to-images` was invisible to anyone who
had not written code against it — which is most people who open a dataset page.

ADR-0014's verification caught none of this, because it checked the feature *type* and the served
asset URLs rather than looking at the page.

## Decision

**One row per image.** The `images` list becomes a scalar `image` column; a document with three
images produces three rows, each carrying that document's `pdf_url`, `text` and `matched_terms`.

## Consequences

- The viewer shows pictures.
- Row count becomes image count: 10 rows became 250 on the pilot.
- **A document's text repeats across its images.** Logically that duplicated 31 MB on the pilot,
  almost all of it one 160 KB document with 190 images. In practice parquet's dictionary encoding
  reduced it to about 90 KB: the published file went from 2.64 MB to 2.73 MB. The redundancy is
  real but costs essentially nothing, and it buys rows that stand alone.
- Grouping by `pdf_url` recovers the per-document view for anyone who wants it.
- "Every published row carries an image" (ADR-0015) now holds by construction rather than by
  filter: a row *is* an image.

## Alternatives rejected

- **Keep the list, add a scalar `preview_image`.** Two columns holding overlapping data, and the
  preview duplicates bytes. The viewer would show one thumbnail per document, which is better than
  JSON but still hides most of the images.
- **Keep the list and document the limitation.** `load_dataset()` users were already fine; only
  the web preview suffered. Rejected because the web preview is how a dataset is judged, and
  "the pictures are there, you just cannot see them" is not a proof of concept for turning PDFs
  into images.
- **Cap the list length.** Worth doing under either shape — 190 images in one cell is unusable —
  but it does not make a list column render.
