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

Membership in FinePDFs does not by itself grant permission to republish a source PDF or the images
inside it. Publication is governed by a conservative, testable policy; artifacts whose
redistribution status cannot be established are excluded from public binary publication and reduced
to a metadata/hash reference. See the dataset card and `docs/` for the takedown contact.

This repository's own code is licensed under Apache-2.0.
