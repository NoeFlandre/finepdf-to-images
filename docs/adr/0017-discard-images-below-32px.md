# ADR-0017: Discard extracted images below 32px on a side

Status: accepted (2026-09-17)

## Context

PDFs embed their table borders and underlines as real image XObjects. The extractor cannot tell them from photographs. Thus the pipeline indexed and published them like any other image.

The team measured the published dataset at revision `272389c` (493 rows):

| min(width, height) | rows |
| --- | --- |
| 1-3px | **178** |
| 4-15px | 24 |
| 16-31px | 14 |
| 32-63px | 60 |
| 64-127px | 25 |
| 128px+ | 192 |

**216 of 493 rows (44%) had a side under 32px.** They form clusters. A reader who scrolled the viewer saw runs like `277x2`, `279x2`, `281x2`, `282x1`. These are page rules from one document.

ADR-0015 established that each published row carries an image. The pipeline met this goal only in part. Each row had an image, and half of them showed a line of one pixel.

## Decision

The extractor discards an extracted image unless **both** sides are at least **32px**.

Apply the rule at the **extract** stage and not at publication. An index full of table rules is not useful for any consumer of that stage. The counts of the extract manifest must describe real images.

## Why 32, and why sides and not area

The team chose the number from the distribution above and not by taste:

- It removes the whole mass of artifacts and nothing else. These are the buckets 1-3px, 4-15px, and 16-31px, with 216 rows.
- **It is in a flat region.** If the threshold is 48, the result changes by 7 rows out of 493. Thus the dataset is not sensitive to the exact number. A threshold that you must defend to the pixel is in the wrong place.
- **64 goes too far.** It drops 60 more images. These are real small pictures: icons, logos, and seals. The cut belongs where the artifacts end. It does not belong deeper.

**Use both sides, not area.** A `600x2` rule has an area of 1200px and is a line. A `33x33` icon has an area of 1089px and is a picture. Area ranks them in the wrong order.

## Consequences

- About 44% of the rows disappear from the published dataset. This is the purpose.
- A document with only rules as images now contributes no rows. The invariant of ADR-0015 already implies this. It also lowers the count of "documents with images" in an honest way.
- The test fixtures moved from images of 2x2 to images of 32x32. The pipeline now discards a 2x2 fixture. A fixture that the code throws away tests nothing.
- Images of one solid color pass this filter. An example is a block of 200x200 white. They are as useless as the rules. This ADR does not address them. This needs a histogram check and not a size check. They were not common enough in the pilot to justify the complexity.
