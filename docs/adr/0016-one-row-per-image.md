# ADR-0016: One row per image, so the viewer shows pictures

Status: accepted (2026-09-17)

## Context

ADR-0015 left the dataset with one row for each document. An `images` column held a list of embedded pictures. `datasets-server` typed that column as intended:

```
feature images -> {"feature": {"_type": "Image"}, "_type": "List"}
```

It served each image as a real asset. The data was correct.

But the **viewer** showed the cell as raw JSON:

```
[{"src":"https://datasets-server.huggingface.co/assets/NoeFlandre/finepdf...
```

The viewer of the Hub renders a *scalar* `Image` feature as a thumbnail. It does not render a list of them. Thus the main feature of a dataset with the name `finepdf-to-images` was invisible. Only a person who had written code against the dataset could see it. Most people who open a dataset page have not done this.

The verification of ADR-0014 did not catch this. It checked the feature *type* and the URLs of the served assets. It did not look at the page.

## Decision

**Use one row for each image.** The `images` list becomes a scalar `image` column. A document with three images gives three rows. Each row carries the `pdf_url`, `text`, and `matched_terms` of that document.

## Consequences

- The viewer shows pictures.
- The row count becomes the image count. On the pilot, 10 rows became 250.
- **The text of a document repeats across its images.** Logically, this duplicated 31 MB on the pilot. Almost all of it was one document of 160 KB with 190 images. In practice, the dictionary encoding of Parquet reduced it to about 90 KB. The published file grew from 2.64 MB to 2.73 MB. The redundancy is real, but it costs almost nothing. It gives rows that stand alone.
- If you group by `pdf_url`, you get the view for each document again.
- "Each published row carries an image" (ADR-0015) is now true by construction. It is not true by a filter. A row *is* an image.

## Alternatives that the team rejected

- **Keep the list and add a scalar `preview_image`.** Two columns hold overlapping data, and the preview duplicates bytes. The viewer shows one thumbnail for each document. This is better than JSON, but it still hides most of the images.
- **Keep the list and document the limitation.** Users of `load_dataset()` were already fine. Only the web preview had a problem. The team rejected this option because people judge a dataset by its web preview. "The pictures are there, but you cannot see them" is not a proof of concept for the conversion of PDFs to images.
- **Cap the length of the list.** This is good under either shape. 190 images in one cell are not usable. But it does not make a list column render.
