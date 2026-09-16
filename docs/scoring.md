# Agriculture relevance scoring

## What it is

A **keyword filter over English text**. Deliberately unclever.

The first version of a filter like this should be something a domain expert can read, disagree
with, and correct — not a model checkpoint whose decisions nobody can audit. Every positive result
carries the exact terms that produced it.

```bash
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
```

## The rule

Terms are grouped into seven concept groups: **crops, livestock, soil, irrigation, forestry,
fisheries, farm management**. A document is relevant when it matches

- at least **2 distinct groups** (breadth — soil *and* irrigation *and* crops together is
  agriculture), **or**
- at least **3 distinct terms within one group** (depth — a wheat agronomy paper that a breadth
  rule alone would miss).

`score` is the number of distinct groups matched. Two rather than one, because one group is exactly
what an incidental mention looks like.

## Matching

Text is normalised before matching: **NFKC** (so full-width and ligature variants collapse onto
plain letters), then **casefold** (not `lower` — it handles ß and Turkish dotted I correctly), then
every non-word run becomes a single space.

Terms match as **whole words or whole phrases**, never substrings. Substring matching would make
`wheat` match "wheaten" and — worse — `cow` match "coward".

Phrases match across punctuation (`"soil, moisture"` matches `soil moisture`) but not across
intervening words (`"soil in the moisture"` does not).

## Words deliberately left out

`bear`, `bull`, `crop`, `culture`, `field`, `harvest`, `plant`, `seed`, `yield`.

Every one is common in agriculture and **more** common elsewhere: crop (photographs), yield
(finance, materials), field (physics, databases), plant (factories), harvest (data), bull and bear
(markets), seed (funding, randomness). They are listed in `EXCLUDED_AMBIGUOUS_TERMS` and emitted in
the run manifest, so the omission reads as a decision rather than an oversight.

This is why "Please crop the image and adjust the field of view" does not score as agriculture.

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
not a claim that keywords are better. A keyword filter has a real ceiling: it cannot recognise a
document about agriculture that never uses the vocabulary, and it will mark a document that merely
*discusses* farming policy in passing. Both failure modes are visible in the evidence, which is the
point.

## Replacing it

`score()` is a pure function over `(text, language)` returning a result with evidence. A
multilingual vocabulary is a data change. A model-based scorer is a new implementation of the same
signature, with `vocabulary_version` bumped so old runs remain interpretable.

The run manifest records the whole vocabulary, the thresholds and the excluded terms, so a threshold
change cannot silently reinterpret an earlier run's output.
