# Provenance, licensing and publication policy

## The premise

**A document in FinePDFs is not a permission to republish it.**

FinePDFs has the ODC-BY license. This license covers the *dataset*: the extracted text, the metadata, and the compilation. It does not cover the copyright of the PDFs that the rows point to. The copyright belongs to the person who published the PDF. An HTTP 200 response is not a license. It means that a server sent bytes. It does not mean that anyone can redistribute the bytes.

Thus the default answer for a retrieved artifact is **no**.

## The decision

One pure function makes the decision: `finepdf_to_images.domain.policy.decide`. It applies these rules in this order:

1. **Incomplete provenance gives `exclude`.** The function drops a row completely when it cannot trace the row to its FinePDFs row and source URL. It does not publish an untraceable metadata row. This check runs *first*. A perfect license does not make an untraceable row publishable.
2. **Bytes give `publish-artifact`** only when all three conditions are true together:
     - The status is `declared-open`.
     - The identifier is on the allow-list.
     - The evidence is `curated-allowlist`.
3. **Everything else gives `metadata-only`.** The function keeps the provenance and the hashes. It keeps no bytes. This is enough to reproduce and verify the run. It does not redistribute the work.

No branch changes an unknown license to an allowed license. No argument makes such a change. This absence is the design. A Hypothesis property asserts it over the full cross-product of status, evidence, and identifier. A comment does not assert it.

## Vocabulary

### Status: what is known

| | |
| --- | --- |
| `unknown` | Nobody established anything. **This is the default. It is also the most common real answer.** |
| `absent` | The source states that there are no terms. |
| `declared-open` | A person positively identified an open license. |
| `declared-restricted` | Terms exist and they forbid redistribution. |
| `incompatible` | Terms exist but they conflict with publication here. |

`unknown` and `absent` are separate values on purpose. "We did not establish any terms" and "the source says there are none" are different claims. If you merge them, an unknown can change to a license without notice.

### Evidence: how well it is known

| | Trusted for bytes? |
| --- | --- |
| `none` | no |
| `url-heuristic` | no |
| `response-header` | no |
| `curated-allowlist` | **yes** |

Only a human decision that is recorded in this repository can support the redistribution of bytes. A license string in a response header shows what a server said. It does not show what a rights holder permits.

### Allow-list

`CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `PDM-1.0`, `public-domain`.

The list is short on purpose. Each entry is a commitment. A longer list is not a better list. The match is exact. `cc-by-4.0` and `" CC-BY-4.0 "` do not match. Thus letter case and whitespace cannot pass an entry through the check.

## Required provenance

The required fields are: `dataset`, `revision`, `config`, `split`, `shard`, `row_index`, `row_id`, `url`.

A row about retrieved bytes also requires `sha256` (`ARTIFACT_PROVENANCE`). The retrieval and extraction stages pass `require_artifact_hash=True`. The value must be a real digest of 64 lowercase hexadecimal characters. A present field is not enough. `metadata-only` is a meaningful fallback only if the metadata identifies *which* bytes it represents. The value `"not-a-hash"` identifies nothing.

Each published row carries all of these fields. Thus you can trace each artifact to the exact FinePDFs row and source URL that it came from.

The check for an empty value depends on the type of the field. It does not test for falsiness. A `row_index` of `0` is a real value. A falsiness check silently drops the first row of each shard. But an `is None` check alone is too loose. With it, `url=False` and `url=[]` look present. Thus the integer fields must be non-negative `int`. A `bool` is not valid, because `bool` is a subclass of `int`. The other fields must be non-blank `str` with no surrounding whitespace. The check refuses padding and does not trim it. The allow-list already refuses `" CC-BY-4.0 "`. The module must be consistent about padding.

Some fields travel with each row on `SourceRecord`, but the policy does **not require** them. They are the crawl date and the extraction metadata of FinePDFs: `date`, `extractor`, `is_truncated`, and the language scores. The redistribution status of a document does not depend on the crawl date or on the quality of the text extraction. If the policy required them, it would exclude a fully traceable row. This contradicts the only reason for `exclude`.

## Limitations

Third parties published the source documents. They used terms that this project does not control and cannot verify at scale. The pipeline represents the artifacts with unknown redistribution status by metadata and hashes only.

The pilot has **no curated allow-list entries**. Thus no artifact meets the condition for byte publication, even after the retrieval stage exists. The result is metadata and hashes only. This is the honest result of a conservative policy on an arbitrary web sample. It is not a gap. See [technical debt](technical-debt.md) TD-004.

## Takedown

To request the removal of an artifact, do one of these actions:

- Open an issue at <https://github.com/NoeFlandre/finepdf-to-images/issues>.
- Use the **Community** tab of the Hugging Face dataset.

The project accepts these requests **without proof of ownership from the requester**. The cost to remove an item that we had no strong claim to publish is much lower than the cost of an error.

## Attribution

> Source documents were identified through
> [HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), licensed ODC-BY.

`policy_summary()` returns this statement, the limitations, and the takedown route in machine-readable form. The publication stage (issue #2) writes them into the dataset card from that function. It does not state them again. Thus the card cannot differ from the code that enforces the policy. No code calls the function until that stage exists. See [technical debt](technical-debt.md) TD-005.
