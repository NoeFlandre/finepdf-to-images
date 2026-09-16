# The pinned FinePDFs input

## What is pinned

| | |
| --- | --- |
| Dataset | [`HuggingFaceFW/finepdfs`](https://huggingface.co/datasets/HuggingFaceFW/finepdfs) |
| Revision | `220bac3acbf07789502c621d2d33952f51ac7f86` |
| Config | `eng_Latn` |
| Split | `train` |
| Shard | `000_00000.parquet` (`data/eng_Latn/train/000_00000.parquet`) |
| Default limit | 100 rows |
| Default strategy | `head` |
| Default seed | `finepdf-to-images/v1` |

Upstream licence: **ODC-BY**. Attribution and the redistribution rules for the *documents behind*
these rows are a separate matter, covered by the publication policy.

!!! note "The shard path in issue #7 does not exist"
    Issue #7 suggested `eng_Latn/train/0000.parquet`. FinePDFs names shards
    `<group>_<index>.parquet` under `data/<config>/<split>/`; the real equivalent is
    `data/eng_Latn/train/000_00000.parquet`. The pipeline uses the real path and validates it.

## How little it reads

The shard is **4.8 GB**: 388,000 rows in **388 row groups of 1,000**. A run reads whole row groups
only, as many as the limit requires, and projects only the eleven columns the pipeline uses. The
default 100-row run therefore touches **one row group**, not the shard, and never the corpus.

A malformed config, split, shard or revision raises `SourceConfigurationError` *before* any network
access. There is no fallback path that widens a read — that behaviour is what the tests in
`tests/integration/test_source_selection.py` exist to pin down.

## Running it

Against the Hub (network, no token needed — FinePDFs is public):

```bash
uv run finepdf-to-images select --limit 100 --out out/select
```

Entirely offline, against any directory laid out like the dataset repository:

```bash
uv run finepdf-to-images select \
  --source-dir tests/fixtures/shards --limit 5 --out out/select
```

## Sampling

`head`
:   Keep the first `limit` rows in shard order. The cheapest bounded read, and the default.

`hash`
:   Keep the `limit` rows whose `sha256(seed + row id)` sorts lowest **within the read window**.
    Stable across runs and machines because it derives from the row's own identity rather than RNG
    state, so a replay needs only the seed.

Both return rows in shard order, so every downstream artifact has one stable ordering.

## Output

`manifest.json`
:   Source reference, sampling parameters, what was read, counts, per-row provenance without the
    document bodies, and a `records_digest` over the selected rows. No timestamp and no machine
    detail: two runs of the same pinned input produce the same bytes.

`records.jsonl`
:   One canonical JSON document per line, including the extracted `text`.

## Limitations

- The default run sees the shard's **first** 100 rows. That is not a random sample of FinePDFs and
  is not described as one anywhere in the output.
- `hash` sampling is random only *within the bounded window*. A corpus-wide sample would require
  reading the corpus. The manifest records the window so the limitation is visible.
- Only `eng_Latn` is exercised. The record carries `language` and `full_doc_lid` so a later
  multilingual run is a configuration change, not a rewrite.
- A pinned revision does not pick up upstream fixes. Changing the pin is a reviewable edit.

## Test fixture

`tests/fixtures/shards/` holds a **synthetic** 20-row shard with the real upstream schema and
5-row row groups. It is not a slice of FinePDFs: committing real rows would create a redistribution
question the publication policy exists to answer carefully, and a synthetic shard can contain
exactly the edge cases the tests need. Regenerate it with:

```bash
uv run python tests/fixtures/build_shard_fixture.py
```
