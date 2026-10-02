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
| Hard ceiling | 5,000 rows (`MAX_LIMIT`) |
| Default seed | `finepdf-to-images/v1` |

The upstream license is **ODC-BY**. The documents behind these rows are a separate matter. The publication policy gives the rules for their attribution and redistribution.

!!! note "The shard path in issue #7 does not exist"
    Issue #7 suggested `eng_Latn/train/0000.parquet`. FinePDFs names the shards `<group>_<index>.parquet` in `data/<config>/<split>/`. The real path is `data/eng_Latn/train/000_00000.parquet`. The pipeline uses the real path and validates it.

## How little the pipeline reads

The shard is **4.8 GB**. It has 388,000 rows in **388 row groups of 1,000 rows**. A run reads whole row groups only. It reads only as many row groups as the limit needs. It projects only the eleven columns that the pipeline uses. Thus the default run of 100 rows touches **one row group**. It does not touch the whole shard, and it never touches the corpus.

The strategy `head` asks the reader for exactly `limit` rows. It keeps the first rows. A wider window gives no benefit. The strategy `hash` rounds up to whole row groups. This is because sampling is meaningful only over a window that is larger than the sample.

The option `--limit` has a maximum of **5,000** rows. Without a maximum, "bounded" is a promise that the code does not keep. A large limit can read all 388 row groups.

!!! tip "A limit up to the row-group size costs nothing extra"
    A row group is the smallest unit that Parquet can fetch. Thus `--limit 1000` reads **exactly the same bytes** as `--limit 100` on this shard. Both read one row group. The manifest shows this: `rows_fetched: 1000` in both cases. To get more candidate documents for the next stages, increase the limit to the row-group size. Do this before you read a second row group.

The `read` block of the manifest reports the **fetch**, not the request. It contains `rows_fetched`, `row_groups_read`, `max_rows_requested`, `shard_total_rows`, and `shard_total_row_groups`. You can audit the bound from the output. You do not have to trust it.

A malformed config, split, shard, or revision raises `SourceConfigurationError`. This error occurs *before* any network access. No fallback path makes a read wider. The tests in `tests/integration/test_source_selection.py` verify this behavior.

## Run the selection

To read from the Hub, use the command below. It needs the network. It does not need a token, because FinePDFs is public.

```bash
uv run finepdf-to-images select --limit 100 --out out/select
```

To work offline, use any directory that has the same layout as the dataset repository:

```bash
uv run finepdf-to-images select \
  --source-dir tests/fixtures/shards --limit 5 --out out/select
```

## Sampling

`head`
:   Keep the first `limit` rows in shard order. This is the cheapest bounded read. It is the default.

`hash`
:   Keep the `limit` rows with the lowest `sha256(seed + row id)` **in the read window**. The result is the same on all runs and machines. It comes from the identity of the row and not from the state of a random number generator. A replay needs only the seed.

Both strategies return the rows in shard order. Thus each later artifact has one stable order.

## Output

`manifest.json`
:   Contains the source reference, the sampling parameters, and the data that the run fetched. It also contains the counts, the provenance of each row without the document bodies, and a `records_digest` of the selected rows. It has no timestamp and no machine detail. Two runs of the same pinned input give the same bytes.

`records.jsonl`
:   Contains one canonical JSON document on each line. It includes the extracted `text`.

## Limitations

- The default run reads the **first** 100 rows of the shard. This is not a random sample of FinePDFs. The output never says that it is.
- The `hash` sampling is random only *in the bounded window*. A sample of the whole corpus needs a read of the corpus. The manifest records the window, so the limitation is visible.
- Only `eng_Latn` is tested. The record has `language` and `full_doc_lid`. Thus a later multilingual run needs a configuration change and not a rewrite.
- A pinned revision does not receive upstream fixes. A change of the pin is an edit that a reviewer can check.

## Test fixture

The directory `tests/fixtures/shards/` holds a **synthetic** shard of 20 rows. It has the real upstream schema and row groups of 5 rows. It is not a slice of FinePDFs. Real rows in the repository can cause a redistribution question. The publication policy exists to answer this question with care. A synthetic shard can also hold the exact edge cases that the tests need. To regenerate the fixture, do this:

```bash
uv run python tests/fixtures/build_shard_fixture.py
```
