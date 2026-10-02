# ADR-0019: Every published row is an image-caption pair

Status: accepted (2026-09-18)

## Context

ADR-0015 established that each published row carries an image. The team looked at the 146 images that this rule produced. About one third were not figures at all. There were 26 scans of pages, 44 halftone fragments from one document, a dozen publisher badges and society seals, and a few author portraits.

Each of them passed every filter that we had. Each is really a large and unique image. The geometric rules, a size floor and a page-spread count, are proxies for "is this a picture". The things that they miss are the things that have the shape of a picture by chance.

39 of the 146 rows carried a caption. This is the line that the author of the document wrote about that figure. A foundation model trains on these rows. The other rows carry the whole text of the document. It says what the *document* is about. It says nothing about the picture.

Each category of junk that the team found by a look through the images had no caption. Each caption that the team found belonged to a real figure.

## Decision

Publish a row only when its image carries a caption.

The dataset is, by construction, image-caption pairs. This is now its defining property. It is no longer an attribute of some rows.

## Why this and not more geometry

It selects on what the author said. It does not select on what the pixels look like. A scanned letter is not a figure because nobody captioned it. The aspect ratio is not the reason. The aspect ratio is how we would have guessed.

It is also one rule. It is not an accumulating set of rules, each with a threshold to tune.

## Consequences

- **The dataset shrinks by about 73%.** 39 rows of the previous 146 survive. It is a fair question if this is a corpus or a demonstration. The honest answer is that a small set of real pairs is more useful for the stated goal than a larger set that is mostly not pairs.
- **The row count now depends on the quality of the caption extraction.** If the team improves the caption reader (#65), the count increases as a side effect. The manifest reports the captioned and uncaptioned counts separately. Thus this coupling stays visible.
- **The pipeline loses good images with unreadable captions.** A figure in a layout of two columns, with a caption that `extract_text` damaged, is exactly the kind of row that we want. The rule drops it. This is the real cost. For this reason #65 landed first.
- The geometric rules stay. They are cheap and correct. They remove items before the caption rule has to.
