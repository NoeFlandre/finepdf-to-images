# Publishing the result

```bash
uv run finepdf-to-images publish \
  --select-manifest out/select/manifest.json \
  --scored out/score/scored.jsonl \
  --retrieved out/retrieve/retrieved.jsonl \
  --documents out/extract/documents.jsonl \
  --images out/extract/images.jsonl \
  --extract-manifest out/extract/manifest.json \
  --out out/publish            # writes the planned files locally for review
```

Nothing above *changes* the Hub — it is read, to work out whether the result is already there —
and `--apply` is what publishes.

## Dry run by default

The dry run does not consult a flag inside an upload — **it never calls the upload at all**.
Assembling the rows, the manifest and the card happens in the pure domain; the adapter's write path
is reached only under `--apply`. That is why "dry-run does not mutate the Hub" is a structural fact
rather than a promise, and the tests assert it on the fake Hub's own call log.

`--out` writes exactly the bytes that would be uploaded, so the card can be read before anyone
decides to publish it.

## What is published

| path | contents |
| --- | --- |
| `README.md` | the dataset card |
| `manifest.json` | source, sampling, counts, policy, digests, encoder versions |
| `data/documents.jsonl` | one row per **scored** document (`documents` config, `all` split) |
| `data/documents_relevant.jsonl` | scored documents with `relevant == true` (`documents` config, `relevant` split) |
| `data/documents_retrieved.jsonl` | scored documents with `retrieved == true` (`documents` config, `retrieved` split) |
| `data/images.jsonl` | one row per extracted image (`images` config, `train` split) |

**Derived splits allow direct loading without client-side filtering.** The `documents` configuration
declares three splits:
- `all` (`data/documents.jsonl`): all scored documents from the sampled shard, preserving failures,
  negatives, and unretrieved rows for auditing.
- `relevant` (`data/documents_relevant.jsonl`): only documents meeting the relevance threshold
  (`relevant == true`), loadable directly with `load_dataset(..., name="documents", split="relevant")`.
- `retrieved` (`data/documents_retrieved.jsonl`): only documents whose source PDF was successfully
  retrieved (`retrieved == true`), loadable directly with `load_dataset(..., name="documents", split="retrieved")`.

**Every scored row is published in the `all` split, not only the retrieved ones.** A row judged irrelevant, or whose
URL was refused, or whose server returned HTML, carries its `failure_reason`. A dataset that
silently drops its failures cannot be used to reproduce the run or to argue with the scorer.

**Extracted document text and `text_sha256` are published under ODC-BY.** Unlike third-party PDF
and image binaries, the extracted text is part of FinePDFs itself (licensed ODC-BY). Publishing `text`
makes the relevance score and `matched_terms` auditable without re-downloading the source shard.
The published `text_sha256` is verified against the UTF-8 text digest at publication time so that
the published text cannot drift from what was scored. Total published text bytes are strictly bounded
in the domain by `MAX_DOCUMENT_TEXT_BYTES` (50 MB cap), counted across **the rows the run actually
publishes**. It used to sum every scored row, which refused a 5000-row sample over 75 MB of text
that belonged almost entirely to documents the run never published. One row per image means a
document's text repeats across its images, and the cap counts every copy.

**Source bytes are uploaded only for allow-listed sources.** The default is still
`metadata-only` — hashes and provenance, not the document — and it applies to the overwhelming
majority of rows. Bytes ship only when the curated allow list in `domain/allowlist.py` clears the
row, which requires a human decision recorded in this repository citing the instrument that makes
the work free to redistribute. See [ADR-0013](adr/0013-publish-allow-listed-artifact-bytes.md).

Three checks stand between a cleared row and an upload, and they are deliberately stricter than
"the caller passed it in":

- the artifact's path must be the content-addressed path for the bytes actually passed, so an
  artifact cannot be filed under another's digest and a published file is self-verifying;
- its digest must belong to a row the policy cleared — swapping the bytes under a cleared path
  does not inherit that path's clearance;
- the total is capped by `MAX_ARTIFACT_BYTES` (64 MB), so a mistake in the allow list cannot become
  an unbounded redistribution. Like the text cap, it is measured over the rows being published, and
  it is enforced in the publish stage beside it.

A fourth check runs in both directions: a manifest claiming `publishes_source_bytes` with no
artifact in the plan is refused, and so is a plan carrying artifacts under a manifest that claims
none. The card's claim and the payload cannot disagree.

A document whose images the policy did not clear publishes no rows at all. This is
`finepdf-to-images`: every published row carries a picture, by construction. What the run declined
is a fact about the run, and the manifest is where it is recorded.

## The card is generated

Its policy and vocabulary sections come from `policy_summary()` and `vocabulary_summary()` — the
same functions that enforce the rules. A card that disagrees with the code is worse than no card,
so it is not written by hand and cannot drift.

The schema table is generated from `DATASET_FIELDS`, the same tuple the published rows are built
from, so a column cannot be added to the data and forgotten in the card.

The card's YAML front matter is a **machine-read contract**, not prose. It declares one `default`
config with a `train` split over the single parquet, and it declares each column's dtype — `image`
above all. Without an explicit `image` dtype the column is inferred as a string and the viewer
shows a struct instead of a picture, which is the whole point of the dataset. A domain test asserts
that the front matter parses as valid YAML and declares the config, the split and the features it
claims.

## The published tree can shrink

A publication is a statement of what the dataset **is**, not a list of files to add. Remote files
the plan does not contain are deleted in the *same commit* as the writes, so no revision is ever a
mixture of the old shape and the new one.

Only paths this stage writes are candidates — `data/`, `images/`, `pdfs/`, `README.md`,
`manifest.json`. A `LICENSE`, a `.gitignore`, or anything a maintainer added through the Hub's web
UI is left alone, and `.gitattributes` is never touched. See
[ADR-0014](adr/0014-a-publication-removes-what-it-does-not-contain.md).

One consequence is worth stating on its own: **`--pdf-root` and `--image-root` are required
whenever the policy cleared a row.** They used to be optional, and omitting one quietly published
a smaller plan. Once a publication also deletes, that same forgotten flag removes already-published
bytes from a public dataset. Publishing metadata only is expressed by clearing nothing, not by
leaving out an argument.

## One row per image

The published table is one row per image, not per document: a document with three images yields
three rows, each repeating its `pdf_url`, `text` and `matched_terms`.

That shape exists because the Hub's viewer renders a scalar `Image` column as a thumbnail but
renders a *list* of images as JSON — so the list shape left the pictures invisible to anyone
browsing the dataset. See [ADR-0016](adr/0016-one-row-per-image.md). Parquet's dictionary encoding
makes the repeated text almost free: 31 MB of logical duplication cost about 90 KB on the pilot.

## Only rows that carry an image

A document with no embeddable image is not published. This is `finepdf-to-images`: a row with no
picture does not show what the pilot is for, and an empty `images` column left a reader unable to
tell "this PDF had no images" from "this PDF had images you may not see".

Images are no longer filtered by licence either — every extracted image is published, and the card
states plainly that most carry no declared licence and gives a takedown route. That is a deliberate
decision by the dataset owner, recorded with its consequences in
[ADR-0015](adr/0015-publish-only-rows-that-carry-an-image.md). ADR-0013 still governs PDF bytes.

## Idempotency

Publishing the same pilot output twice is an **exact no-op**: no second commit, and the existing
revision is reported. Sameness is decided by content — the SHA-256 of each planned file against
what the Hub holds — not by a timestamp or a run id. The manifest deliberately carries no
timestamp, because a clock reading would make this claim depend on when you ran it rather than on
what you produced.

## Verification

After an upload the Hub is read back and each file's digest compared against what was sent. A
mismatch is reported and the command exits non-zero rather than claiming success.

Verification covers **both halves** of the commit. A stale file that is still being served is as
much a failed publication as a write that did not land: the dataset would keep serving the old
shape while the run reported success.

The Hub reports a **git blob id** for an ordinary file and a content SHA-256 only for an LFS
object, so the comparison accepts either identity. This matters more than it sounds: none of the
six files published here is large enough to be an LFS object, so matching on SHA-256 alone meant
nothing ever matched — every successful publication would have reported "verification FAILED" and
exited 1, and a re-run would never have been recognised as a no-op. A file with neither identity is
still treated as *not matching*, so a publication re-uploads rather than silently skipping
something that may have changed.

## Credentials

No token is read, stored, logged or passed by this project's code. `huggingface_hub` resolves
credentials itself from the environment or the user's stored login. A test greps the adapter's own
source for `token=`, `HF_TOKEN`, `use_auth_token` and `api_key`, and another checks no published
file contains anything resembling a credential.

## Limits worth knowing before you republish

- **Retrieval is not reproducible.** It depends on what third-party servers return on the day; a
  2023 crawl's URLs decay. The run recorded here retrieved 16 of 52.
- **Extracted image bytes are not portable across machines** ([TD-007](technical-debt.md)).
  Regenerating on a different platform changes every image hash. The manifest records the encoder
  that produced the run.
- Selection, scoring, and every manifest *are* byte-identical across runs.
