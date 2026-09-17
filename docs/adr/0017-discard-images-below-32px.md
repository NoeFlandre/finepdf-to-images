# ADR-0017 — Discard extracted images below 32px on a side

Status: accepted (2026-09-17)

## Context

PDFs embed their table borders and underlines as real image XObjects. The extractor cannot tell
them from photographs, so they were indexed and published like any other image.

Measured on the published dataset at revision `272389c` (493 rows):

| min(width, height) | rows |
| --- | --- |
| 1–3px | **178** |
| 4–15px | 24 |
| 16–31px | 14 |
| 32–63px | 60 |
| 64–127px | 25 |
| 128px+ | 192 |

**216 of 493 rows (44%) had a side under 32px.** They cluster, so a reader scrolling the viewer
met runs like `277x2`, `279x2`, `281x2`, `282x1` — page rules from one document.

ADR-0015 established that every published row carries an image. That goal was only half met: every
row had an image, and half of them showed a one-pixel line.

## Decision

An extracted image is discarded unless **both** sides are at least **32px**.

Applied at the **extract** stage, not at publication: an index full of table rules is not useful to
any consumer of that stage either, and the extract manifest's counts should describe real images.

## Why 32, and why sides rather than area

Chosen on the distribution above rather than by taste:

- It removes the entire artifact mass — the 1–3px, 4–15px and 16–31px buckets, 216 rows — and
  nothing else.
- **It sits in a flat region.** Raising it to 48 changes the result by 7 rows out of 493, so the
  dataset is not sensitive to the exact number. A threshold that has to be defended to the pixel
  is a threshold in the wrong place.
- **64 would go too far.** It drops a further 60 images, which are genuine small pictures — icons,
  logos, seals. The cut belongs where the artifacts end, not deeper.

**Both sides, not area.** A `600x2` rule has 1200px of area and is a line; a `33x33` icon has
1089px and is a picture. Area ranks them backwards.

## Consequences

- Roughly 44% of rows disappear from the published dataset. That is the point.
- A document whose images are all rules now contributes no rows at all, which ADR-0015's invariant
  already implies and which lowers the "documents with images" count honestly.
- Test fixtures moved from 2x2 to 32x32 images. A 2x2 fixture is now discarded by the pipeline, and
  a fixture the code throws away tests nothing.
- Solid-colour images — a 200x200 block of white — pass this filter and are just as useless. Not
  addressed here; it needs a histogram check rather than a size check, and it was not measurably
  common enough in the pilot to justify the complexity.
