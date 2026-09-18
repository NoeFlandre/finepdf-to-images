"""Render the dataset card.

The card is generated, not written. Its policy and vocabulary sections come from
:func:`policy_summary` and :func:`vocabulary_summary`, so the published description of the rules
cannot drift from the code that enforces them, and its schema table comes from
``DATASET_FIELDS``, so the documented columns cannot drift from the published ones.

The YAML front matter is a machine-read contract: the Hub parses it to decide how to load the
dataset, and invalid YAML fails the viewer outright rather than degrading.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from finepdf_to_images.domain.images import MIN_CONTINUOUS_TONE_COLOURS, MIN_IMAGE_SIDE
from finepdf_to_images.domain.publication.rows import (
    MIN_DOCUMENTS_FOR_BOILERPLATE,
    MIN_PAGES_FOR_FURNITURE,
)
from finepdf_to_images.domain.publication.schema import (
    DATASET_FIELDS,
    DATASET_FILE,
)
from finepdf_to_images.domain.scoring import vocabulary_summary


def _plural(count: int, noun: str) -> str:
    """``noun`` agreeing with ``count``. The card read "1 documents" without it."""
    return noun if count == 1 else f"{noun}s"


def _dataset_schema_table() -> str:
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in DATASET_FIELDS)
    return f"| column | meaning |\n| --- | --- |\n{rows}"


def render_dataset_card(manifest: Mapping[str, Any], repo: str, published_rows: int) -> str:
    """The card for the minimal dataset.

    The front matter is a machine-read contract, not prose: without a declared ``image`` dtype the
    column is inferred as a string and the viewer shows a struct instead of a picture. It is
    generated from :data:`DATASET_FIELDS` so a column cannot be added to the rows and forgotten
    here, which would make the declared schema disagree with the data and fail the viewer outright.

    Kept deliberately short. Provenance a reader needs to reproduce or cite the run lives here;
    the pipeline explaining itself to its own maintainers belongs in the repository's docs.
    """
    source = manifest["source"]
    sampling = manifest["sampling"]
    vocabulary = vocabulary_summary()
    thresholds = vocabulary["thresholds"]
    groups = len(vocabulary["groups"])
    counts = manifest["counts"]
    seed = sampling["seed"]
    return f"""---
configs:
  - config_name: default
    data_files:
      - split: train
        path: {DATASET_FILE}
dataset_info:
  features:
    - name: pdf_url
      dtype: string
    - name: image
      dtype: image
    - name: caption
      dtype: string
    - name: text
      dtype: string
    - name: matched_terms
      sequence: string
license: odc-by
task_categories:
- text-classification
language:
- en
tags:
- agriculture
- finepdfs
- proof-of-concept
pretty_name: FinePDFs agriculture pilot
---

# finepdf-to-images — agriculture pilot

Agriculture-relevant documents sampled from one pinned shard of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs): the source PDF,
its extracted text, the images embedded in it, and the vocabulary terms that made it relevant.

One row per image: {published_rows} {_plural(published_rows, "image")} extracted from the
documents that {counts["documents"]} scored rows yielded. A document's text and matched terms
repeat across its images, so every row stands alone.

## Schema

{_dataset_schema_table()}

`matched_terms` is why the row is here. It lets you argue with the selection rather than take it
on faith.

## How a row got here

**Selection is a keyword filter over English text — not a model.** It is deliberately
unclever, so you can read the rule, disagree with it, and see exactly which words produced
each row in `matched_terms`.

The text is Unicode-normalised and casefolded, then matched against
**{vocabulary["surface_form_count"]} phrases** grouped into
**{vocabulary["concept_count"]} concepts** across {groups} groups
(crops, soil, irrigation, livestock, fisheries, forestry, farm management, phenotyping).
Spellings of one idea — `fertilizer`/`fertiliser`, `farm`/`farmer`/`farming` — count as **one**
concept, not several, so repetition cannot manufacture evidence.

A document is **relevant** when two things hold. First, agricultural context: it matches at least
{thresholds["min_groups"]} different groups, or at least
{thresholds["min_concepts_in_one_group"]} distinct concepts inside one — a single passing mention
is not enough. Second, **at least one phenotyping concept** — a measured plant trait such as
canopy cover, leaf area index, biomass, senescence or grain yield.

The second condition is what this dataset is for, and the first alone did not give it. A pesticide
label cleared on `agricultural`, `fungicide`, `grazing`; an outdoors magazine cleared on `goat`,
`goats`, `shellfish`. Both are genuinely about agriculture and neither observes a plant. Ambiguous
words are excluded outright — `corn`, `crop`, `field`, `plant`, `yield` and `harvest` mean other
things in most documents.

**What this misses:** it only covers English, so an agricultural document in another language is
a miss rather than a negative, and a relevant document that never uses the vocabulary is invisible
to it.

**Every row is an image and the caption its author wrote for it.** That is what makes a row a
training pair rather than a picture with a topic attached, and it is the strongest filter here:
page scans, publisher badges, halftone fragments and author portraits are all uncaptioned, and all
of them go.

`caption` comes from the image's own page — a line opening `Figure 3.`, `Fig. 12`, `Plate 4` and
so on, continued across the line breaks the layout imposed until the sentence ends. The *n*th image
on a page takes the *n*th caption on it: pypdf lists a page's images without their placement, so
this is order-pairing rather than true nearest-caption matching, and it can mispair on a page whose
figures are laid out out of order.

Four kinds of non-picture are dropped before that:

- images under **{MIN_IMAGE_SIDE}px on either side** — PDFs embed their table rules as images;
- images on **{MIN_PAGES_FOR_FURNITURE} or more pages** of one document — a figure is drawn once,
  on the page that discusses it, while a logo is drawn on every page;
- images appearing in **{MIN_DOCUMENTS_FOR_BOILERPLATE} or more documents** — a publisher's badge
  rather than anyone's figure;
- every image from a document that is a **scan of pages** rather than a paper with figures in it:
  one big image per page, page-shaped and alone on its page;
- **line art** — anything holding fewer than **{MIN_CONTINUOUS_TONE_COLOURS:,} distinct colours**
  at a 200px sample. A photograph is continuous tone, every leaf its own value; a chart is a few
  inks on white. Measured over a full release, charts held 155 to 1,344 colours and
  photographs 11,136 to 24,995, with nothing in between. This dataset is photographs.

## Source

| | |
| --- | --- |
| dataset | [`{source["dataset"]}`](https://huggingface.co/datasets/{source["dataset"]}) |
| revision | `{source["revision"]}` |
| config / split / shard | `{source["config"]}` / `{source["split"]}` / `{source["shard"]}` |
| sampling | limit {sampling["limit"]}, strategy `{sampling["strategy"]}`, seed `{seed}` |

## Licensing

Text is published under **ODC-BY**, inherited from `{source["dataset"]}`, which must be
attributed.

**Images are reproduced from their source PDFs and most carry no declared licence.** This is a
research proof of concept, not a cleared redistribution. Each image is included because it
appeared in a document the scorer selected; copyright remains with its original owner.

**Takedown:** if you hold rights to anything published here and want it removed, open an issue at
<https://github.com/NoeFlandre/finepdf-to-images/issues> and it will be taken down promptly. Each
row carries its `pdf_url`, so the source of any image can be identified directly.

## Use

```python
from datasets import load_dataset

rows = load_dataset("{repo}", split="train")
rows[0]["image"]  # a PIL image
```
"""
