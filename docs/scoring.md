# Agriculture relevance scoring

## What it is

The score is a **keyword filter over English text**. It is simple on purpose.

The first version of such a filter must be readable. A domain expert must be able to read it, disagree with it, and correct it. A model checkpoint is not readable, and nobody can audit its decisions. Each positive result carries the exact terms that caused it.

```bash
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
```

## Structure

The vocabulary has the structure **`group -> concept -> surface forms`**. It is not `group -> terms`. The extra level is necessary. A flat version counted `farm`, `farmer`, `farmers`, and `farming` as four independent pieces of evidence. Thus *"Farmers who farm and love farming"* passed a threshold that must require three **different ideas**. The depth counts concepts. The surface forms are only spellings of one concept.

## The rule

There are seven groups: **crops, livestock, soil, irrigation, forestry, fisheries, farm management**. A document is relevant when it matches one of these conditions:

- It matches at least **2 distinct groups** (breadth). Soil, irrigation, and crops together are agriculture.
- It matches at least **3 distinct concepts in one group** (depth). A breadth rule alone does not find a wheat agronomy paper.

The `score` is the number of distinct groups that match. The minimum is two and not one, because one group is what an incidental mention looks like.

### Matching consumes text

The filter matches the forms **longest phrase first**. It claims the span of each match. Thus shorter forms cannot match the same text again.

Without this rule, one phrase satisfies the breadth rule alone. These are two examples:

- *"Pasture management notes"* matched `pasture` and `pasture management`.
- *"Fish farming"* matched `farming` in farm management and `fish farming` in fisheries.

In each example, one phrase gives two groups. `MIN_GROUPS` exists to prevent this.

The consumption is **for each occurrence, not for each form**. A rule that is based on sets can drop anything that a longer match contains. Such a rule removes real evidence. In *"Fish farming and dryland farming in the district"*, `farming` occurs alone. Thus farm management is real evidence, and the document is relevant.

### Weak concepts

The depth rule needs at least one concept that is **not a bare commodity name**. A list of crop or species names is good *breadth* evidence. But it does not show that a document is about agriculture. These are two examples:

- *"Commodity index weights: maize, soybean, sugarcane, wheat"* has four concepts. It is a finance document. It is **not relevant**.
- *"Wheat, barley and sorghum cultivar trials"* has the same crop names and a practice concept. It is **relevant**.

`WEAK_CONCEPTS` lists these concepts. The run manifest carries the list.

## Matching

The filter normalizes the text before it matches. It does these steps in this order:

1. It applies **NFKC**. Full-width and ligature variants become plain letters.
2. It applies **casefold**. It does not use `lower`, because `casefold` handles the letter ß and the Turkish dotted I correctly.
3. It changes each run of non-word characters to one space.

The separator class is `[\W_]` and not `[^\w]`. The class `\w` includes the underscore. Thus `soil_moisture` and `drip_irrigation` stayed joined, and the filter could not match them. Extracted PDF text, table headers, and code listings use underscores often.

The filter matches terms as **whole words or whole phrases**. It never matches substrings. A substring match makes `wheat` match "wheaten". It also makes `cow` match "coward".

A phrase matches across punctuation. For example, `"soil, moisture"` matches `soil moisture`. A phrase does not match across other words. For example, `"soil in the moisture"` does not match.

## Words that the vocabulary leaves out

`EXCLUDED_AMBIGUOUS_TERMS` maps each omitted term to the *reason*:

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

**A test enforces this list. It is not decoration.** The module refuses to import when one of these terms is a surface form. Thus, if an editor adds `corn` again, the build fails. The tests do not pass in that case. When the concept is still necessary, a disambiguating phrase carries it. Examples are `selective logging`, `timber harvest`, and `smallholder farming`.

The filter does not score these texts as agriculture:

- *"Please crop the image and adjust the field of view."*
- *"Enable structured logging across the server farm before the release."*
- *"Well logging showed the water table at 12 m depth."*
- *"Potato salad, rice bowls and corn chips: a restaurant menu with grain bread."*

## Language

The vocabulary covers **`eng_Latn` only**. The interface states this. Each result records these fields:

- `language`: the language that the caller gives for the document. It comes from the FinePDFs row. It is **not** a detection.
- `vocabulary_language`: the language that the vocabulary covers.
- `language_matches_vocabulary`: whether the vocabulary can apply at all.

The score of a Portuguese document against an English vocabulary is a **miss**. It is not a negative. If a pipeline mixes the two, it gets a language bias that it never declared.

The scorer does not change its decision because of `language`. It records the mismatch and it scores the document. Thus the mismatch is visible in the output and not hidden in a branch.

## What it is not

The scorer has no model download, no embedding, and no opaque classifier. This is a deliberate limit of the first proof of concept. It is not a claim that keywords are better.

A keyword filter has a real ceiling. This document lists the known failures and does not patch them. Each patch removes real coverage.

**False positives**

- A **colonoscopy preparation diet sheet** from the pinned shard scored relevant on `barley`, `poultry`, and `shellfish`. These are three groups, all in a food context. If the vocabulary excludes each term with a food sense, the crop vocabulary loses most of its terms.
- **Veterinary and toxicology text** scores relevant. An example is *"Bacterial culture from the wound; pesticide poisoning. Veterinary referral."* The words `veterinary` and `pesticide` are ordinary clinical words.
- A **contrived menu that uses agricultural practice language** clears the depth rule. An example is *"Sowing season menu: paddy rice, millet porridge..."*. The word `sowing` is a real practice term.

**False negatives**

- *"Poultry broiler houses and swine piggery biosecurity on the farm."* is real agriculture. It scores **not relevant**. It has one group, and `farm` is excluded because of "server farm" and "farm out". This trade is deliberate. The term `farm` caused more software false positives than agricultural true positives.

**In scope on purpose (not a false positive)**: Forestry and fisheries are groups of their own. Thus the filter keeps a pure-ecology document about deforestation, afforestation, and soil erosion.

The evidence shows each of these cases. `matched_terms` gives the exact reason that the filter kept a row. A reviewer can disagree at a glance.

### Observed rate

The first 1,000 rows of the pinned shard give **51 relevant rows (5.1%)**. The hits include provincial agricultural gazettes, extension-service crop bulletins, a fungicide label, cattle-industry stewardship material, and carbon-registry farming projects.

## Replace the scorer

`score()` is a pure function over `(text, language)`. It returns a result with evidence. A multilingual vocabulary is a data change. A model-based scorer is a new implementation of the same signature. In that case, increase `vocabulary_version` so that old runs stay interpretable.

The run manifest records the whole vocabulary, the thresholds, and the excluded terms. Thus a threshold change cannot silently change the meaning of an earlier run. Increase `VOCABULARY_VERSION` when the vocabulary or the thresholds change.

The scoring manifest also records an `input_digest` with the `scored_digest`. Thus you can trace a `scored.jsonl` to the exact selection that produced its input.
