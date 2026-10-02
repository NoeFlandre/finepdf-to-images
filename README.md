# finepdf-to-images

This is a small proof-of-concept pipeline. It finds FinePDF documents that are relevant to agriculture. It publishes their source PDFs and images.

The pipeline does these steps:

1. It reads **one pinned shard** of [HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs).
2. It keeps a bounded sample of rows.
3. It scores each row for agriculture relevance. It uses the extracted text of the row.
4. It retrieves only the selected source PDFs.
5. It extracts the images that are embedded in these PDFs.
6. It publishes the bounded result to [NoeFlandre/finepdf-to-images-poc](https://huggingface.co/datasets/NoeFlandre/finepdf-to-images-poc).

The pipeline does not download, list, or copy the FinePDFs corpus.

For the meaning of the technical terms, see the [glossary](docs/glossary.md).

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

The image contains no credentials. Only the commands that need a Hugging Face token get it. Give the token at run time with `-e HF_TOKEN`.

## Layout

| Layer | Package | Rule |
| --- | --- | --- |
| Domain | `finepdf_to_images.domain` | Pure. No network, filesystem, PDF or Hub imports. |
| Adapters | `finepdf_to_images.adapters` | All side effects. Thin and injectable. |
| Composition | `finepdf_to_images.cli`, `finepdf_to_images.pipeline` | Connects the adapters to the domain. |

A test enforces this rule. The test in `tests/architecture/` parses each module. The build fails when a module breaks the rule or when an import cycle occurs.

## Documentation

The full documentation is in [`docs/`](docs/index.md). It includes the [quickstart](docs/quickstart.md), the [architecture](docs/architecture.md), the [quality gates](docs/quality.md), the [decisions](docs/adr/index.md), the [technical debt](docs/technical-debt.md), and the [glossary](docs/glossary.md).

To read the documentation in a browser, do this:

```bash
uv run --group docs mkdocs serve
```

## Licensing and publication

**WARNING: A document in FinePDFs is not a permission to republish it.** FinePDFs has the ODC-BY license. This license covers the dataset: the text, the metadata, and the compilation. It does not cover the copyright of the PDFs that the rows point to. An HTTP 200 response is not a license.

The default action is refusal. The function `domain/policy.decide()` publishes bytes only when both of these conditions are true:

- A `declared-open` status has an allow-listed identifier.
- A person recorded a decision in this repository.

For all other rows, the pipeline keeps only provenance and hashes. The pipeline drops a row completely when it cannot trace the row back to its FinePDFs row. A Hypothesis property proves that no input changes an unknown license to an allowed license.

The pilot has no allow-list entries. Nothing meets the condition, even after the retrieval stage exists. For the full rules, the limitations statement, and the takedown route, see [the policy](docs/policy.md).

The code of this repository has the Apache-2.0 license.
