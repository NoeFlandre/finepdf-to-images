# ADR-0021 — Publish photographs, not line art

Status: accepted (2026-09-18)

## Context

ADR-0019 made every row an image–caption pair, and the pairs are genuinely figures. But about two
thirds of them are charts: scatter plots, box plots, line graphs, schematic diagrams.

A foundation model for agriculture and phenotyping learns what a plant looks like from
photographs of plants, organs, plots and fields. It learns nothing about a canopy from a box plot
of disease incidence, however relevant the study.

## The measurement

Distinct RGB values, after thumbnailing each image to a 200px bound, over all 39 rows of the first
image–caption release:

| | distinct colours | mean saturation |
| --- | --- | --- |
| line-art plots, box plots, schematics | **155 – 1,344** | 0.0 – 0.4 |
| photographs (rice grains, field sites, specimens) | **11,136 – 24,995** | 63 – 161 |

Nothing fell between 1,344 and 3,038.

A photograph is continuous tone: every leaf and every shadow is its own value. A chart is a handful
of ink colours on white, however elaborate it looks.

## Decision

An image is published only when it holds at least **2,000 distinct colours** at the pinned sample
size. The threshold sits in the measured gap rather than inside either population.

The count is computed in the adapter, because it needs decoded pixels and Pillow is banned from the
domain by the architecture check. A missing count means "unknown" and publishes the row: the rule
takes effect on re-extraction, like the page dimensions before it.

## Why not saturation

Saturation is the metric this looks like it should be, and it fails. A coloured bar chart
(`Fig. 3. Average fresh-weight contents of ABA and GA3`) scores 22.9 — higher than a genuine
specimen photograph at 11.9. Colour count separates that same pair by two orders of magnitude:
297 against 7,823.

An earlier pixel hypothesis in this repository — that halftone tiles would have few colours — was
filed before being measured and turned out to be wrong. This one was measured on every published
row first.

## Consequences

- **39 rows become 13.** Replayed over the current release: 26 dropped, every one a chart.
- **Two charts survive**: `Fig. 4. Estimated resource overlap` (3,038) and `Table 7: Perceptions of
  respondents` (7,823). Anti-aliased gradients and a scanned table push them over. Roughly 90%
  precision.
- The caption text would catch those two (`Relationship between…`, `Distribution of…` against
  `Photographed sample images of…`). Deliberately not added: one rule that is 90% right is easier
  to reason about than two that are 95% right together, and the caption vocabulary would need its
  own tuning.
- **The pilot now publishes a single-digit number of rows.** That is a demonstration, not a
  dataset, and the fix is a bigger shard rather than a looser filter — every stage is bounded and
  resumable, so raising the 5,000-row limit is the honest next step. The row count should be read
  as "the filters work", not as "the corpus collapsed".
