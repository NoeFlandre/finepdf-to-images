# ADR-0015: Publish only rows that carry an image, and stop filtering images by license

Status: accepted (2026-09-17)

## Context

ADR-0014 and the minimal Parquet layout were done. Then the published dataset had 52 rows. Only **one** row carried a visible image. The pipeline extracted images from ten documents. Nine of them showed an empty `images` column, because their source was not on the redistribution allow-list.

Thus a reader who opened a dataset with the name `finepdf-to-images` saw 51 rows with no picture. The reader could not know the reason: "this PDF had no images" or "this PDF had images that you cannot see".

## Decision

1. **Do not publish a document that has no embeddable image.** Apply the filter to what actually embeds. Thus each published row is a row in which a reader can see something. The published table has no empty `images` column.
2. **Do not filter images by license.** Publish each extracted image.

The dataset owner took decision 2. The owner took it explicitly and knew the consequences. This ADR records it. The decision is not implicit in a diff, because it reverses the posture of ADR-0013 for images.

## Consequences

- The pilot publishes 10 rows and not 52. It publishes 250 images and not 190. They come from 10 sources and not 1. The number is 250 and not 259. The run extracted 269 image references. These deduplicate to 250 distinct images, because the same picture can appear on several pages.
- **Nine of these ten sources declared no license.** They include commercial publishers, an academic journal, and a university extension service. The copyright stays with them. This dataset reproduces their images without permission, as a research proof of concept.
- Disclosure and responsiveness replace the filter as the obligation. The card states plainly that most images carry no declared license. The card has a takedown route. Each row publishes its `pdf_url`. Thus anyone can trace an image to its source and remove it on request.
- ADR-0013 still governs the **PDF** bytes. This ADR changes the treatment of images only. The minimal layout no longer republishes PDFs.
- Anyone who reuses this dataset inherits that exposure. The card says so. It is not a license.

## Alternatives that the team rejected

- **Keep the license filter and filter the rows on extracted images.** The result is ten rows, and nine still show nothing. This is the original confusion, but smaller.
- **Keep the license filter and filter the rows on published images.** The result is one row. This dataset is really a function of the allow-list. It is not a function of the pipeline.
- **Grow the allow-list first.** This is the principled fix. It is still open. If more sources are cleared, the filter can be strict and the dataset does not collapse. It needs a judgement about the license of each source. Nobody has made it. It was not a reason to continue to ship rows that a reader cannot use.
