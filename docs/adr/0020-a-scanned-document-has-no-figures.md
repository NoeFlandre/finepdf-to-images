# ADR-0020 — A scanned document publishes no figures

Status: accepted (2026-09-18)

## Context

The extractor's own docstring predicted this case:

> A scanned document whose every page is one big image will produce one image per page, which is
> correct but is not the same thing as "the figures in this document".

26 of 146 published images were photographs of pages — scanned letters and typed correspondence.
One document contributed 20 of them.

## Decision

An image is *page-shaped* when its aspect ratio is within 10% of its page's, it is the only image on
that page, and its long side is at least 600px.

A **document** is judged a scan when at least half of its images are page-shaped and at least three
are. None of a scanned document's images are published.

## Why per document

The per-image test alone flags a legitimate row: a `581x722` full-width table, captioned
`Table 7: Perceptions of respondents…`, has roughly its page's proportions. Dropping it would lose
exactly the kind of row this corpus wants.

A scanned document is scanned throughout. Measured on a real run:

```
18/20 page-like (90%)   a scanned document
 1/1  page-like (100%)  the captioned table, alone in its document
 0/4, 0/9, 0/16, 0/5    ordinary documents with real figures
```

The count requirement is what protects the single case; the share is what makes it a pattern.

## Consequences

- This is the first exclusion that is **not** a function of what the extraction index already held:
  it needs the page dimensions, which are now recorded per image. An older index cannot be
  re-judged, and `scanned_document_pages` returns nothing rather than guessing when the dimensions
  are absent.
- A born-digital paper whose figures happen to be full-page loses them if three or more are.
  Accepted; the share requirement makes it unlikely.
- Under ADR-0019 most of these rows would have gone anyway, since a page scan is uncaptioned. The
  rule is kept because it is independent of caption quality: it states what the document *is*.
