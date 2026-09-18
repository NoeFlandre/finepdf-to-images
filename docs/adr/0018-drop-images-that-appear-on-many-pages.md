# ADR-0018 — Drop images that appear on many pages

Status: accepted (2026-09-18)

## Context

ADR-0017 removed images too small to be pictures. What remained included images that are the right
size and still not pictures: institutional logos, header rules, footer marks, watermarks.

A logo is drawn in the header of every page. Its bytes are identical each time, so it has one
digest, and `_embedded_images` already carried each digest once per document. **Carrying it once is
still carrying it.** In the pilot's 277 rows, marks of this kind sit beside the real figures,
published with the document's agricultural terms attached — a confident example of exactly the
wrong thing for a corpus meant to teach what agriculture looks like.

Deduplication solved "the same logo eight times". It did not solve "the logo".

## Decision

Within one document, an image whose digest appears on **3 or more distinct pages** publishes no
row.

Counted over distinct pages rather than occurrences: a figure repeated twice on its own page is
still one figure, while a mark appearing once per page across five pages is furniture.

Scoped to one document. Two documents that happen to share a stock photograph are not each other's
furniture.

## Why three

A figure is drawn once, on the page that discusses it. A logo is drawn on every page. There is very
little in between, which is what makes the threshold cheap — it is not a tuned parameter sitting in
a continuum, it separates two populations.

Two is deliberately below the line. A two-page leaflet's single photograph can legitimately appear
on both sides, and a cover image repeated on a back page is not worth the false positive.

## Consequences

- A document that genuinely reuses one photograph across three or more pages loses it. Accepted:
  in practice that image is a header, and the corpus is better off without the class than with the
  exception.
- The rule needs no new parsing. `page_index` is already recorded for every extracted image, so
  page spread is a count over data the extraction stage already produces.
- It can only remove rows, and the count is reported, so the effect is measurable rather than
  asserted.
- Like the size floor, it is a content-based exclusion applied at publication rather than at
  extraction: the extraction index keeps every occurrence, so the decision can be revisited
  without re-fetching anything.


## Amendment (2026-09-18) — counting across documents too

The rule above counts pages within one document, which cannot see a publisher's badge: within any
one document a CrossMark button or a society seal appears exactly once, which is what a figure looks
like.

A digest appearing in **2 or more documents** is now dropped as boilerplate. Two rather than three,
because unlike the page rule there is no leaflet case to protect: a figure published in two
documents of a 5,000-row crawl is far more likely to be a shared badge than a coincidence.

Measured honestly: in the run this was written against, **one** digest appeared in more than one
document. The badges are visibly the same logo to a reader but are re-encoded per publisher, so
byte identity does not catch them. Catching a logo re-rendered at another resolution needs
perceptual hashing — a new dependency and a similarity threshold to tune — and is deliberately out
of scope.

The two counts are reported separately in the manifest, so the effect of each is visible rather
than merged.
