# ADR-0021: Publish photographs, not line art

Status: accepted (2026-09-18)

## Context

ADR-0019 made each row an image-caption pair. The pairs are really figures. But about two thirds of them are charts: scatter plots, box plots, line graphs, and schematic diagrams.

A foundation model for agriculture and phenotyping learns what a plant looks like from photographs of plants, organs, plots, and fields. It learns nothing about a canopy from a box plot of disease incidence, even if the study is relevant.

## The measurement

The team counted the distinct RGB values after it made a thumbnail of each image with a bound of 200px. It did this for all 39 rows of the first image-caption release:

| | distinct colors | mean saturation |
| --- | --- | --- |
| line-art plots, box plots, schematics | **155 - 1,344** | 0.0 - 0.4 |
| photographs (rice grains, field sites, specimens) | **11,136 - 24,995** | 63 - 161 |

Nothing fell between 1,344 and 3,038.

A photograph has continuous tone. Each leaf and each shadow has its own value. A chart has a few ink colors on white, even if it looks elaborate.

## Decision

Publish an image only when it has at least **2,000 distinct colors** at the pinned sample size. The threshold is in the measured gap. It is not inside either population.

The adapter computes the count, because it needs decoded pixels. The architecture check bans Pillow from the domain. A missing count means "unknown", and the pipeline publishes the row. The rule takes effect at a new extraction, in the same way as the page dimensions before it.

## Why not saturation

Saturation looks like the metric that we should use. It fails. A colored bar chart (`Fig. 3. Average fresh-weight contents of ABA and GA3`) scores 22.9. This is higher than a real photograph of a specimen at 11.9. The color count separates the same pair by two orders of magnitude: 297 against 7,823.

An earlier pixel hypothesis in this repository was that halftone tiles have few colors. The team filed it before it measured it. It was wrong. The team measured this hypothesis on each published row first.

## Consequences

- **39 rows become 13.** The team replayed the rule over the current release. It dropped 26 rows. Each one was a chart.
- **Two charts survive.** They are `Fig. 4. Estimated resource overlap` (3,038) and `Table 7: Perceptions of respondents` (7,823). Anti-aliased gradients and a scanned table push them over the threshold. The precision is about 90%.
- The caption text catches those two (`Relationship between...` and `Distribution of...` against `Photographed sample images of...`). The team did not add this rule on purpose. One rule that is 90% right is easier to reason about than two rules that are together 95% right. The caption vocabulary needs its own tuning.
- **The pilot now publishes a number of rows with one digit.** This is a demonstration and not a dataset. The fix is a bigger shard and not a looser filter. Each stage is bounded and can resume. Thus to increase the limit of 5,000 rows is the honest next step. Read the row count as "the filters work". Do not read it as "the corpus collapsed".
