# Quickstart

## Local

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

## CLI

```bash
uv run finepdf-to-images --help
uv run finepdf-to-images version
```

Select a bounded sample from the pinned shard. See [Pinned input](source.md).

```bash
uv run finepdf-to-images select --limit 100 --out out/select
```

To work offline, use the committed fixture shard:

```bash
uv run finepdf-to-images select --source-dir tests/fixtures/shards --limit 5 --out out/select
```

Score the selected rows for agriculture relevance. See [Relevance scoring](scoring.md).

```bash
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
```

Retrieve the source PDFs of the relevant rows. See [Retrieving PDFs](retrieval.md).

```bash
uv run finepdf-to-images retrieve \
  --scored out/score/scored.jsonl \
  --select-manifest out/select/manifest.json \
  --relevant-only --out out/retrieve
```

Extract the images that are embedded in these PDFs. See [Extracting images](images.md).

```bash
uv run finepdf-to-images extract \
  --retrieved out/retrieve/retrieved.jsonl \
  --pdf-root out/retrieve --out out/extract
```

Publish the result. See [Publishing](publishing.md). The command below is a **dry run**. To upload, add `--apply`.

```bash
uv run finepdf-to-images publish \
  --select-manifest out/select/manifest.json --scored out/score/scored.jsonl \
  --retrieved out/retrieve/retrieved.jsonl --documents out/extract/documents.jsonl \
  --images out/extract/images.jsonl --extract-manifest out/extract/manifest.json \
  --out out/publish
```

The exit codes are a stable contract:

| Code | Meaning |
| --- | --- |
| `0` | The command completed. |
| `1` | The command ran, but the requested work failed. |
| `2` | Usage error (unknown command, or wrong or missing arguments). |

## Docker

```bash
docker build -t finepdf-to-images .
docker run --rm finepdf-to-images --help
```

The image contains no credentials. Give a Hugging Face token at run time only when a command needs it:

```bash
docker run --rm -e HF_TOKEN finepdf-to-images version
```

## Documentation

To read the documentation in a browser, do this:

```bash
uv run --group docs mkdocs serve
```
