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
| `data/documents.jsonl` | one row per **scored** document |
| `data/images.jsonl` | one row per extracted image |

**Every scored row is published, not only the retrieved ones.** A row judged irrelevant, or whose
URL was refused, or whose server returned HTML, carries its `failure_reason`. A dataset that
silently drops its failures cannot be used to reproduce the run or to argue with the scorer.

**No source PDF or image bytes are uploaded.** The policy ships no curated allow-list entries, so
every artifact resolves to `metadata-only`: hashes and provenance, not the documents. The manifest
records this as `publishes_source_bytes: false` and the card says it in its second paragraph.

## The card is generated

Its policy and vocabulary sections come from `policy_summary()` and `vocabulary_summary()` — the
same functions that enforce the rules. A card that disagrees with the code is worse than no card,
so it is not written by hand and cannot drift.

The schema tables are generated from `DOCUMENT_FIELDS` and `IMAGE_FIELDS`, and a test asserts every
documented field is actually emitted.

The card's YAML front matter is a **machine-read contract**, not prose. Without explicit `configs:`
declaring `documents` (`data/documents.jsonl`) and `images` (`data/images.jsonl`) as separate
configurations, the Hub auto-detects `data/*` as a single split, attempts to concatenate files with
incompatible schemas, and fails with `CastError` (disabling parquet conversion and the Dataset Viewer).
A domain test asserts that the front matter parses as valid YAML and explicitly declares both configs.

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
four files published here is large enough to be an LFS object, so matching on SHA-256 alone meant
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
