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

The command above does not *change* the Hub. It reads the Hub to find out if the result is already there. The option `--apply` publishes.

## Dry run by default

The dry run does not check a flag inside an upload. **It never calls the upload.** The pure domain assembles the rows, the manifest, and the card. The write path of the adapter runs only with `--apply`. Thus "the dry run does not change the Hub" is a structural fact and not a promise. The tests assert it on the call log of the fake Hub.

The option `--out` writes exactly the bytes that the command would upload. Thus a person can read the card before the decision to publish.

## What the command publishes

| path | contents |
| --- | --- |
| `README.md` | the dataset card |
| `manifest.json` | source, sampling, counts, policy, digests, encoder versions |
| `data/documents.jsonl` | one row for each **scored** document (`documents` config, `all` split) |
| `data/documents_relevant.jsonl` | scored documents with `relevant == true` (`documents` config, `relevant` split) |
| `data/documents_retrieved.jsonl` | scored documents with `retrieved == true` (`documents` config, `retrieved` split) |
| `data/images.jsonl` | one row for each extracted image (`images` config, `train` split) |

**Derived splits let a user load the data directly without filtering on the client.** The `documents` config declares three splits:

- `all` (`data/documents.jsonl`): all scored documents from the sampled shard. It keeps the failures, the negatives, and the rows that were not retrieved, for audit.
- `relevant` (`data/documents_relevant.jsonl`): only the documents that meet the relevance threshold (`relevant == true`). Load it with `load_dataset(..., name="documents", split="relevant")`.
- `retrieved` (`data/documents_retrieved.jsonl`): only the documents with a successfully retrieved source PDF (`retrieved == true`). Load it with `load_dataset(..., name="documents", split="retrieved")`.

**The `all` split contains each scored row, not only the retrieved rows.** A row that the scorer judged irrelevant carries its `failure_reason`. So does a row whose URL was refused and a row whose server returned HTML. A dataset that silently drops its failures cannot reproduce the run. It also cannot be used to dispute the scorer.

**The command publishes the extracted document text and `text_sha256` under ODC-BY.** The extracted text is part of FinePDFs (ODC-BY). The binaries of third-party PDFs and images are not. The published `text` makes the relevance score and `matched_terms` auditable. A user does not have to download the source shard again. At publication time, the command verifies the published `text_sha256` against the UTF-8 text digest. Thus the published text cannot differ from the scored text.

The domain strictly bounds the total published text bytes with `MAX_DOCUMENT_TEXT_BYTES` (50 MB cap). The count covers **the rows that the run publishes**. In the past, the count included each scored row. It refused a sample of 5000 rows with 75 MB of text, and almost all of that text belonged to documents that the run never published. There is one row for each image. Thus the text of a document repeats across its images. The cap counts each copy.

**The command uploads source bytes only for allow-listed sources.** The default is still `metadata-only`: hashes and provenance, not the document. It applies to almost all rows. The command ships bytes only when the curated allow-list in `domain/allowlist.py` clears the row. This requires a human decision that is recorded in this repository. The decision must cite the instrument that makes the work free to redistribute. See [ADR-0013](adr/0013-publish-allow-listed-artifact-bytes.md).

Three checks stand between a cleared row and an upload. They are stricter than "the caller passed it in":

- The path of the artifact must be the content-addressed path for the bytes that the caller passed. Thus no artifact can be filed under the digest of another artifact. A published file verifies itself.
- The digest of the artifact must belong to a row that the policy cleared. If you swap the bytes under a cleared path, the new bytes do not get the clearance of that path.
- `MAX_ARTIFACT_BYTES` (64 MB) caps the total. Thus a mistake in the allow-list cannot become an unbounded redistribution. As for the text cap, the count covers the rows that the run publishes. The publish stage enforces it next to the text cap.

A fourth check runs in both directions. The command refuses a manifest that claims `publishes_source_bytes` when the plan has no artifact. It also refuses a plan that carries artifacts under a manifest that claims none. Thus the claim of the card and the payload cannot disagree.

When the policy did not clear the images of a document, the command publishes no rows for that document. This is `finepdf-to-images`: each published row carries a picture, by construction. What the run declined is a fact about the run. The manifest records it.

## The card is generated

The policy section and the vocabulary section of the card come from `policy_summary()` and `vocabulary_summary()`. These are the functions that enforce the rules. A card that disagrees with the code is worse than no card. Thus nobody writes the card by hand, and it cannot differ from the code.

The schema table comes from `DATASET_FIELDS`. The published rows come from the same tuple. Thus a person cannot add a column to the data and forget it in the card.

The YAML front matter of the card is a **machine-read contract**. It is not prose. It declares one `default` config with a `train` split over the single Parquet file. It declares the dtype of each column, in particular `image`. Without an explicit `image` dtype, the viewer infers a string column. Then the viewer shows a struct and not a picture. The picture is the main purpose of the dataset. A domain test asserts that the front matter parses as valid YAML. It also asserts that the front matter declares the config, the split, and the features that it claims.

## The published tree can shrink

A publication states what the dataset **is**. It is not a list of files to add. The command deletes the remote files that the plan does not contain in the *same commit* as the writes. Thus no revision is a mixture of the old shape and the new shape.

Only the paths that this stage writes can be deleted: `data/`, `images/`, `pdfs/`, `README.md`, `manifest.json`. The command does not touch a `LICENSE`, a `.gitignore`, or anything that a maintainer added through the web UI of the Hub. It never touches `.gitattributes`. See [ADR-0014](adr/0014-a-publication-removes-what-it-does-not-contain.md).

One consequence needs its own statement. **`--pdf-root` and `--image-root` are required when the policy cleared a row.** In the past they were optional. If you omitted one, the command silently published a smaller plan. Now a publication also deletes. Thus the same forgotten flag removes bytes that the command already published to a public dataset. To publish metadata only, clear nothing. Do not omit an argument.

## One row for each image

The published table has one row for each image and not for each document. A document with three images gives three rows. Each row repeats its `pdf_url`, `text`, and `matched_terms`.

This shape exists for this reason. The viewer of the Hub shows a scalar `Image` column as a thumbnail. It shows a *list* of images as JSON. Thus the list shape left the pictures invisible to a person who browses the dataset. See [ADR-0016](adr/0016-one-row-per-image.md). The dictionary encoding of Parquet makes the repeated text almost free. On the pilot, 31 MB of logical duplication cost about 90 KB.

## Only rows that carry an image

The command does not publish a document that has no embeddable image. This is `finepdf-to-images`. A row with no picture does not show the purpose of the pilot. An empty `images` column also stopped the reader from knowing the reason: "this PDF had no images" or "this PDF had images that you cannot see".

The command also does not filter the images by license. It publishes each extracted image. The card states that most images have no declared license. The card also gives a takedown route. The dataset owner made this decision on purpose. [ADR-0015](adr/0015-publish-only-rows-that-carry-an-image.md) records it with its consequences. ADR-0013 still governs the PDF bytes.

## Idempotency

When you publish the same pilot output twice, the second publication is an **exact no-op**. It makes no second commit. It reports the existing revision. The command decides sameness by content. It compares the SHA-256 of each planned file with the file on the Hub. It does not use a timestamp or a run id. The manifest has no timestamp on purpose. A clock reading makes the claim depend on the time of the run and not on what you produced.

## Verification

After an upload, the command reads the Hub back. It compares the digest of each file with the data that it sent. When a digest does not match, the command reports it and exits with a non-zero code. It does not claim success.

The verification covers **both halves** of the commit. A stale file that the Hub still serves is a failed publication, in the same way as a write that did not land. The dataset keeps the old shape while the run reports success.

The Hub reports a **git blob id** for an ordinary file. It reports a content SHA-256 only for an LFS object. Thus the comparison accepts either identity. This is important. None of the six published files is large enough to be an LFS object. When the comparison used SHA-256 alone, nothing matched. Each successful publication reported "verification FAILED" and exited with 1. A second run was never recognized as a no-op. The command treats a file with neither identity as *not matching*. Thus a publication uploads the file again. It does not silently skip a file that can have changed.

## Credentials

The code of this project does not read, store, log, or pass a token. `huggingface_hub` resolves the credentials itself, from the environment or from the stored login of the user. One test searches the own source of the adapter for `token=`, `HF_TOKEN`, `use_auth_token`, and `api_key`. Another test checks that no published file contains anything that looks like a credential.

## Limits to know before you republish

- **The retrieval is not reproducible.** It depends on what the third-party servers return on the day. The URLs of a crawl from 2023 decay. The run recorded here retrieved 16 of 52.
- **The bytes of the extracted images are not the same on all machines** ([TD-007](technical-debt.md)). If you regenerate on a different platform, each image hash changes. The manifest records the encoder that produced the run.
- The selection, the scoring, and each manifest *are* byte-identical across runs.
