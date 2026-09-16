# finepdf-to-images

Tiny proof-of-concept pipeline for finding agriculture-relevant FinePDF documents and publishing
their source PDFs and images.

It reads **one pinned shard** of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), keeps a bounded
sample of rows, scores each row for agriculture relevance from its extracted text, retrieves only
the selected source PDFs, extracts their embedded images, and publishes the bounded result to
[NoeFlandre/finepdf-to-images-poc](https://huggingface.co/datasets/NoeFlandre/finepdf-to-images-poc).

It never downloads, enumerates, or mirrors the FinePDFs corpus.

## Quickstart

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

```bash
uv run finepdf-to-images --help
uv run finepdf-to-images select --source-dir tests/fixtures/shards --limit 5 --out out/select
```

```bash
docker build -t finepdf-to-images .
docker run --rm finepdf-to-images --help
```

The image contains no credentials. A Hugging Face token is passed at run time (`-e HF_TOKEN`) only
by the commands that need one.

## Layout

| Layer | Package | Rule |
| --- | --- | --- |
| Domain | `finepdf_to_images.domain` | Pure. No network, filesystem, PDF or Hub imports. |
| Adapters | `finepdf_to_images.adapters` | All side effects, thin and injectable. |
| Composition | `finepdf_to_images.cli`, `finepdf_to_images.pipeline` | Wires adapters into the domain. |

The rule is executable: `tests/architecture/` parses every module and fails the build on a
violation or an import cycle.

## Documentation

Full docs live in [`docs/`](docs/index.md): [quickstart](docs/quickstart.md),
[architecture](docs/architecture.md), [quality gates](docs/quality.md),
[decisions](docs/adr/index.md), and [technical debt](docs/technical-debt.md).

```bash
uv run --group docs mkdocs serve
```

## Licensing and publication

**Being in FinePDFs is not permission to republish a document.** FinePDFs is ODC-BY, which covers
the dataset — text, metadata, compilation — not the copyright in the PDFs its rows point at. An
HTTP 200 is not a licence either.

So the default is refusal. `domain/policy.decide()` publishes bytes only when a `declared-open`
status carries an allow-listed identifier *and* rests on a human decision recorded in this
repository; everything else is reduced to provenance and hashes; a row that cannot be traced back
to its FinePDFs row is dropped entirely. A Hypothesis property proves no input turns an unknown
licence into an allowed one.

The pilot ships no allow-list entries, so nothing would clear that bar even once the retrieval
stage exists. See [the policy](docs/policy.md) for the full rules, the limitations statement, and
the takedown route.

This repository's own code is licensed under Apache-2.0.
