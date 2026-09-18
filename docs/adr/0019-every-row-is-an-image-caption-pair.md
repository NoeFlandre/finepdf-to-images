# ADR-0019 — Every published row is an image–caption pair

Status: accepted (2026-09-18)

## Context

ADR-0015 established that every published row carries an image. Looking at the 146 images that
rule produced, roughly a third were not figures at all: 26 scans of pages, 44 halftone fragments
from a single document, a dozen publisher badges and society seals, and a handful of author
portraits.

Each had been passed by every filter we had, because each is genuinely a large, unique image. The
geometric rules — a size floor, a page-spread count — are proxies for "is this a picture", and the
things they miss are the things that happen to be picture-shaped.

Meanwhile 39 of the 146 rows carried a caption: the line the document's own author wrote about that
figure. Those rows are what a foundation model trains on. The rest carry the document's whole text,
which says what the *document* is about and nothing about the picture.

Every junk category found by looking through the images was uncaptioned. Every caption found
belonged to a real figure.

## Decision

A row is published only when its image carries a caption.

The dataset is, by construction, image–caption pairs. That is now its defining property rather than
an attribute of some rows.

## Why this over more geometry

It selects on what the author said rather than on what the pixels look like. A scanned letter is
not a figure because nobody captioned it, not because of its aspect ratio; the aspect ratio is how
we would have guessed.

It is also one rule rather than an accumulating set of them, each with a threshold to tune.

## Consequences

- **The dataset shrinks by about 73%.** 39 rows of the previous 146 would survive. Whether that is
  a corpus or a demonstration is a fair question, and the honest answer is that a small set of real
  pairs is more useful for the stated goal than a larger set that is mostly not pairs.
- **The row count now depends on caption-extraction quality.** Improving the caption reader (#65)
  raises the count as a side effect. The manifest reports captioned and uncaptioned counts
  separately so that coupling stays visible.
- **Good images with unreadable captions are lost.** A figure in a two-column layout whose caption
  `extract_text` mangled is exactly the kind of row we want, and it goes. This is the real cost, and
  it is why #65 landed first.
- The geometric rules stay. They are cheap, they are correct, and they remove things before the
  caption rule has to.
