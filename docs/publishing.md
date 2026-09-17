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
in the domain by `MAX_DOCUMENT_TEXT_BYTES` (50 MB cap), counted across **every file the plan
publishes**. The derived splits republish the same rows, so a document that is relevant and
retrieved carries its text three times; measuring only the `all` set would under-count the
publication by that factor — the pilot uploads 30.1 MB of text where such a count reports 24.7 MB.

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
  an unbounded redistribution.

A fourth check runs in both directions: a manifest claiming `publishes_source_bytes` with no
artifact in the plan is refused, and so is a plan carrying artifacts under a manifest that claims
none. The card's claim and the payload cannot disagree.

Rows that were not cleared keep `image: null` and `pdf: null` rather than disappearing — the
dataset should say what it declined to publish.

## The card is generated

Its policy and vocabulary sections come from `policy_summary()` and `vocabulary_summary()` — the
same functions that enforce the rules. A card that disagrees with the code is worse than no card,
so it is not written by hand and cannot drift.

The schema tables are generated from `DOCUMENT_FIELDS` and `IMAGE_FIELDS`, and a test asserts every
documented field is actually emitted.

The card's YAML front matter is a **machine-read contract**, not prose. Without explicit `configs:`
declaring `documents` (with its `all`, `relevant`, and `retrieved` splits) and `images`
(`train` split) as separate configurations, the Hub auto-detects `data/*` as a single split,
attempts to concatenate files with incompatible schemas, and fails with `CastError` (disabling
parquet conversion and the Dataset Viewer). A domain test asserts that the front matter parses
as valid YAML and explicitly declares both configs with their respective splits and data files.

## Idempotency

Publishing the same pilot output twice is an **exact no-op**: no second commit, and the existing
revision is reported. Sameness is decided by content — the SHA-256 of each planned file against
what the Hub holds — not by a timestamp or a run id. The manifest deliberately carries no
timestamp, because a clock reading would make this claim depend on when you ran it rather than on
what you produced.

## Verification

After an upload the Hub is read back and each file's digest compared against what was sent. A
mismatch is reported and the command exits non-zero rather than claiming success.

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
