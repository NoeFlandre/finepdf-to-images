# ADR-0020: A scanned document publishes no figures

Status: accepted (2026-09-18)

## Context

The docstring of the extractor predicted this case:

> A scanned document whose every page is one big image will produce one image per page, which is
> correct but is not the same thing as "the figures in this document".

26 of 146 published images were photographs of pages: scanned letters and typed correspondence. One document contributed 20 of them.

## Decision

An image is *page-shaped* when all three conditions are true:

- Its aspect ratio is within 10% of the aspect ratio of its page.
- It is the only image on that page.
- Its long side is at least 600px.

The pipeline judges a **document** as a scan when at least half of its images are page-shaped and at least three of them are page-shaped. The pipeline publishes none of the images of a scanned document.

## Why for each document

The test for each image alone flags a legitimate row. A full-width table of `581x722` has a caption `Table 7: Perceptions of respondents...`. It has about the proportions of its page. If the pipeline drops it, it loses exactly the kind of row that this corpus wants.

A scanned document is scanned throughout. This is the measurement on a real run:

```
18/20 page-like (90%)   a scanned document
 1/1  page-like (100%)  the captioned table, alone in its document
 0/4, 0/9, 0/16, 0/5    ordinary documents with real figures
```

The count requirement protects the single case. The share makes it a pattern.

## Consequences

- This is the first exclusion that is **not** a function of what the extraction index already held. It needs the page dimensions. The index now records them for each image. The pipeline cannot judge an older index again. `scanned_document_pages` returns nothing when the dimensions are absent. It does not guess.
- A born-digital paper with full-page figures loses them if it has three or more. The team accepted this. The share requirement makes it unlikely.
- Under ADR-0019, most of these rows would have gone anyway, because a page scan has no caption. The team keeps the rule because it does not depend on the quality of the captions. It states what the document *is*.
