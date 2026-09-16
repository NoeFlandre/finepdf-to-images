# Agriculture relevance scoring

## What it is

A **keyword filter over English text**. Deliberately unclever.

The first version of a filter like this should be something a domain expert can read, disagree
with, and correct — not a model checkpoint whose decisions nobody can audit. Every positive result
carries the exact terms that produced it.

```bash
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
```

## Structure

The vocabulary is **`group → concept → surface forms`**, not `group → terms`. The extra level is
load-bearing. A flat version counted `farm`, `farmer`, `farmers` and `farming` as four independent
pieces of evidence, so *"Farmers who farm and love farming"* cleared a threshold meant to require
three **different ideas**. Depth counts concepts; surface forms are just spellings of one.

## The rule

Seven groups: **crops, livestock, soil, irrigation, forestry, fisheries, farm management**. A
document is relevant when it matches

- at least **2 distinct groups** (breadth — soil *and* irrigation *and* crops together is
  agriculture), **or**
- at least **3 distinct concepts within one group** (depth — a wheat agronomy paper that a breadth
  rule alone would miss).

`score` is the number of distinct groups matched. Two rather than one, because one group is exactly
what an incidental mention looks like.

### Subsumption

A matched term contained in a longer matched term is dropped before the groups are counted.
Without it, **one phrase satisfies the breadth rule on its own**: *"Pasture management notes"*
matched both `pasture` and `pasture management`, and *"Fish farming"* matched `farming` in farm
management as well as `fish farming` in fisheries — two groups from a single phrase, which is
exactly what `MIN_GROUPS` exists to prevent.

## Matching

Text is normalised before matching: **NFKC** (so full-width and ligature variants collapse onto
plain letters), then **casefold** (not `lower` — it handles ß and Turkish dotted I correctly), then
every non-word run becomes a single space.

The separator class is `[\W_]`, not `[^\w]`: `\w` includes the underscore, so `soil_moisture` and
`drip_irrigation` were glued together and silently unmatchable. Extracted PDF text, table headers
and code listings use underscores routinely.

Terms match as **whole words or whole phrases**, never substrings. Substring matching would make
`wheat` match "wheaten" and — worse — `cow` match "coward".

Phrases match across punctuation (`"soil, moisture"` matches `soil moisture`) but not across
intervening words (`"soil in the moisture"` does not).

## Words deliberately left out

`EXCLUDED_AMBIGUOUS_TERMS` maps each omitted term to *why*:

| term | more commonly means |
| --- | --- |
| `bear`, `bull` | markets |
| `cereal` | breakfast, food packaging |
| `corn`, `grain`, `potato`, `rice` | food, menus, commodity trading |
| `crop` | photographs, image editing |
| `culture` | organisational, microbiological |
| `farm` | server farm, farm out, farm team |
| `field` | physics, databases, sport |
| `harvest` | data harvesting |
| `logging` | software logs, well logging |
| `plant` | factories, power plants |
| `seed` | funding rounds, randomness |
| `timber` | construction, mining supports |
| `water table` | hydrogeology, civil engineering |
| `yield` | finance, materials science |

**This list is enforced, not decorative.** The module refuses to import if any of these appears as a
surface form, so a later edit adding `corn` back fails the build instead of passing every test.
Where the concept is still wanted, a disambiguating phrase carries it: `selective logging`,
`timber harvest`, `smallholder farming`.

This is why none of these score as agriculture:

- *"Please crop the image and adjust the field of view."*
- *"Enable structured logging across the server farm before the release."*
- *"Well logging showed the water table at 12 m depth."*
- *"Potato salad, rice bowls and corn chips: a restaurant menu with grain bread."*

## Language

The vocabulary covers **`eng_Latn` only**, and the interface says so rather than pretending
otherwise. Every result records:

- `language` — what the caller said the document is (from the FinePDFs row; **not** a detection),
- `vocabulary_language` — what the vocabulary actually covers,
- `language_matches_vocabulary` — whether the vocabulary could meaningfully apply at all.

Scoring a Portuguese document against an English vocabulary is a **miss**, not a negative.
Conflating the two is how a pipeline quietly acquires a language bias it never declared.

The scorer does not change its decision based on `language`; it records the mismatch and scores
anyway, so the mismatch is visible in the output instead of hidden in a branch.

## What it is not

No model download, no embedding, no opaque classifier. That is a deliberate limit of the first POC,
not a claim that keywords are better.

A keyword filter has a real ceiling. It cannot recognise a document about agriculture that never
uses the vocabulary, and it will flag one that merely mentions farming in passing. A real example
from the pinned shard: a **colonoscopy preparation diet sheet** scored relevant on `barley`,
`poultry` and `shellfish` — three groups, all food context. Excluding every term with a food sense
would gut the crop vocabulary, so this one is accepted and documented rather than patched away.

Both failure modes are visible in the evidence, which is the point: `matched_terms` on that row
says exactly why it was kept, so a reviewer can disagree in one glance.

### Observed rate

On the pinned shard's first 1,000 rows: **51 relevant (5.1%)**. Hits include provincial agricultural
gazettes, extension-service crop bulletins, a fungicide label, cattle-industry stewardship material
and carbon-registry farming projects.

## Replacing it

`score()` is a pure function over `(text, language)` returning a result with evidence. A
multilingual vocabulary is a data change. A model-based scorer is a new implementation of the same
signature, with `vocabulary_version` bumped so old runs remain interpretable.

The run manifest records the whole vocabulary, the thresholds and the excluded terms, so a threshold
change cannot silently reinterpret an earlier run's output. `VOCABULARY_VERSION` is bumped whenever
either changes.

The scoring manifest also records an `input_digest` alongside `scored_digest`, so a `scored.jsonl`
can be traced back to the exact selection that produced its input.
