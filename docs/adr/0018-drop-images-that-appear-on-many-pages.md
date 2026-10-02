# ADR-0018: Drop images that appear on many pages

Status: accepted (2026-09-18)

## Context

ADR-0017 removed the images that are too small to be pictures. Some of the remaining images have the right size, but they are still not pictures. They are institutional logos, header rules, footer marks, and watermarks.

A logo is drawn in the header of each page. Its bytes are identical each time. Thus it has one digest. `_embedded_images` already carried each digest once for each document. **To carry it once is still to carry it.** The 277 rows of the pilot contain marks of this kind next to the real figures. The pipeline published them with the agricultural terms of the document. This is a confident example of the wrong thing for a corpus that must teach what agriculture looks like.

Deduplication solved "the same logo eight times". It did not solve "the logo".

## Decision

In one document, an image whose digest appears on **3 or more distinct pages** publishes no row.

The rule counts distinct pages and not occurrences. A figure that repeats twice on its own page is still one figure. A mark that appears once on each of five pages is furniture.

The scope is one document. Two documents can share a stock photograph by chance. They are not furniture for each other.

## Why three

A figure is drawn once, on the page that discusses it. A logo is drawn on every page. Very little is in between. This makes the threshold cheap. It is not a tuned parameter in a continuum. It separates two populations.

Two is below the line on purpose. The single photograph of a leaflet of two pages can legitimately appear on both sides. A cover image that repeats on a back page is not worth the false positive.

## Consequences

- A document that reuses one photograph on three or more pages loses it. The team accepted this. In practice that image is a header. The corpus is better without the class than with the exception.
- The rule needs no new parsing. The extraction already records `page_index` for each extracted image. Thus the page spread is a count over data that the extraction stage already produces.
- The rule can only remove rows. The manifest reports the count. Thus the effect is measurable and not only asserted.
- As for the size floor, the rule is a content-based exclusion. It applies at publication and not at extraction. The extraction index keeps each occurrence. Thus the team can review the decision without a new fetch.

## Amendment (2026-09-18): count across documents too

The rule above counts pages in one document. It cannot see the badge of a publisher. In any one document, a CrossMark button or a society seal appears exactly once. This is what a figure looks like.

The pipeline now drops a digest that appears in **2 or more documents** as boilerplate. The number is two and not three. Unlike the page rule, there is no leaflet case to protect. A figure that two documents of a crawl of 5,000 rows publish is much more likely a shared badge than a coincidence.

The measurement is honest. In the run that the team wrote this against, **one** digest appeared in more than one document. A reader sees that the badges are the same logo. But each publisher re-encodes them. Thus byte identity does not catch them. To catch a logo that was rendered again at another resolution, the team needs perceptual hashing. This needs a new dependency and a similarity threshold to tune. It is out of scope on purpose.

The manifest reports the two counts separately. Thus the effect of each is visible. They are not merged.
