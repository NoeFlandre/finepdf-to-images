# ADR-0004: One pinned shard, one bounded window

Status: accepted (2026-09-16)

## Context

FinePDFs has about 3,575 files in about 1,800 language configs. The pilot shard, `data/eng_Latn/train/000_00000.parquet`, is **4.8 GB**. It holds **388,000 rows in 388 row groups of 1,000 rows**. A proof of concept that accidentally streams the config, or the corpus, has failed before it starts.

Issue #7 recommended `eng_Latn/train/0000.parquet`. This path does not exist. Upstream names the shards `<group>_<index>.parquet` in `data/<config>/<split>/`. The real path is `data/eng_Latn/train/000_00000.parquet`.

## Decision

- Pin the dataset to commit `220bac3acbf07789502c621d2d33952f51ac7f86`. Never use a branch name.
- Address exactly one shard through a validated `SourceRef`. A malformed config, split, shard, or revision raises an error before any network access. It never makes the read wider.
- Read **whole row groups only**. Read only as many row groups as the limit needs. Project only the eleven columns that the pipeline uses. The default run of 100 rows touches one row group.
- Use 100 rows as the default. Cap `--limit` at 5,000. Without a maximum, "bounded" is a promise that the code does not keep. A large limit reads all 388 row groups.
- Let `head` ask for exactly `limit` rows. Only `hash` becomes wider, to whole row groups. Only `hash` needs a window that is larger than the sample.
- Offer two deterministic sampling strategies. `head` keeps the first rows in shard order. `hash` keeps the rows with the lowest `sha256(seed + row id)` in the read window. Both return the results in shard order. Thus the later artifacts have one stable order.

## Consequences

- A bounded run is cheap and repeatable. The manifest records enough data to reproduce it exactly: dataset, revision, config, split, shard, path, limit, seed, strategy, columns, and a `read` block. The `read` block describes the **fetch** and not the request. It contains the rows fetched, the row groups read, and the size of the shard. Thus you can audit the bound from the output. You do not have to trust it.
- `hash` samples only in the bounded window and not in the whole shard. This is a deliberate trade. A random sample of the whole corpus needs a read of the corpus. The manifest records the window. Thus the limitation is visible and not implied.
- A pinned revision means that a run does not receive upstream fixes. This is the purpose. A change of the pin is an edit that a reviewer can check.
- The 100 rows that the default run reads are the first 100 rows of the shard. They are not a random sample of FinePDFs. Do not describe them as one.
