# ADR-0004 — One pinned shard, one bounded window

Status: accepted (2026-09-16)

## Context

FinePDFs is ~3,575 files across roughly 1,800 language configs. The pilot shard alone,
`data/eng_Latn/train/000_00000.parquet`, is **4.8 GB** holding **388,000 rows in 388 row groups of
1,000**. A proof of concept that accidentally streams the config, let alone the corpus, has failed
before it starts.

Issue #7 recommended `eng_Latn/train/0000.parquet`. That path does not exist: upstream names shards
`<group>_<index>.parquet` under `data/<config>/<split>/`. The real equivalent is
`data/eng_Latn/train/000_00000.parquet`.

## Decision

- Pin the dataset to commit `220bac3acbf07789502c621d2d33952f51ac7f86`, never a branch name.
- Address exactly one shard through a validated `SourceRef`. A malformed config, split, shard or
  revision raises before any network access and never widens the read.
- Read **whole row groups only, and only as many as the limit requires**, projecting just the
  eleven columns the pipeline uses. The default 100-row run touches one row group.
- Default to 100 rows and cap `--limit` at 5,000. Without a ceiling "bounded" is a promise the
  code does not keep: a large enough limit walks all 388 row groups.
- Let `head` ask for exactly `limit` rows. Only `hash` widens to whole row groups, because only
  `hash` needs a window larger than the sample.
- Offer two deterministic sampling strategies: `head` (first rows in shard order) and `hash`
  (lowest `sha256(seed + row id)` within the read window). Both return results in shard order, so
  downstream artifacts have one stable ordering.

## Consequences

- A bounded run is cheap and repeatable, and the manifest records enough to reproduce it exactly:
  dataset, revision, config, split, shard, path, limit, seed, strategy, columns, and a `read` block
  describing the **fetch** rather than the request — rows fetched, row groups read, and the shard's
  own size, so the bound is auditable from the output instead of taken on trust.
- `hash` samples only within the bounded window, not the whole shard. That is a deliberate trade:
  a corpus-wide random sample would require reading the corpus. The manifest records the window so
  the limitation is visible rather than implied.
- Pinning a revision means a run does not pick up upstream fixes. That is the point; changing the
  pin is a reviewable edit.
- The 100 rows the default run sees are the shard's first 100, which are not a random sample of
  FinePDFs and must not be described as one.
