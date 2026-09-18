# Target samples

`samples_target/` holds nine smartphone photographs of crops, kept as a **picture of the target**:
the kind of image this pipeline is ultimately meant to surface from documents. They are reference
material for judging output, not fixtures — no test reads them, and nothing in the pipeline
produces or consumes them.

They exist because "agriculture-relevant image" is easy to assert and hard to check. A run that
yields page scans, logos and decorative borders passes every mechanical gate in this repo and still
misses the point. These photographs make that failure visible: a reviewer can put them next to a
run's `images/` and see whether the two belong to the same category.

## What they are

In-field, hand-held, uncontrolled-lighting photographs — the opposite of a figure lifted from a PDF.
Several show disease or stress symptoms; several show healthy canopy, fruit or bare-soil stand
establishment, because a useful corpus is not only symptomatic close-ups.

| File | Subject |
| --- | --- |
| `01-cucurbit-leaf-downy-mildew-closeup.jpg` | Cucurbit leaf, chlorotic patches and necrosis |
| `02-chili-pepper-fruit-lesions-in-field.jpg` | Chili pepper fruit with lesions, held in the field |
| `03-sugarcane-leaf-chlorosis-closeup.jpg` | Sugarcane leaves, chlorotic striping |
| `04-wheat-canopy-quadrat-overhead.jpg` | Wheat canopy, overhead through a measurement quadrat |
| `05-mango-canopy-foliage.jpg` | Mango canopy foliage |
| `06-seedling-rows-bare-soil-overhead.jpg` | Seedling rows on bare soil, overhead, stand establishment |
| `07-lychee-fruit-on-branch.jpg` | Lychee fruit on the branch |
| `08-mango-fruit-on-neutral-background.jpg` | Single mango, neutral background |
| `09-maize-ear-held-in-field.jpg` | Maize ear, husk opened, held in the field |

Names describe the **content**, not the provenance or the date. A file called
`June 20 2024 Image from <person>.jpg` tells a reader nothing about whether a run's output resembles
it, and carries a person's name into the repository for no benefit.

## What they are not

- Not a benchmark, and not a labelled dataset. There are no annotations, and nine photographs
  measure nothing.
- Not part of the published dataset. Nothing here is pushed to the Hub, and the
  [publication policy](policy.md) governs that dataset, not this directory. These files are shared
  working material committed to a public repository on the understanding that their contributors
  agreed to that; no licence for them is recorded here, so treat them as all-rights-reserved and ask
  before reusing them elsewhere.
- Not an input format. The pipeline extracts images **embedded in PDFs** — see
  [extracting images](images.md). Nothing here is ever fed to it.
