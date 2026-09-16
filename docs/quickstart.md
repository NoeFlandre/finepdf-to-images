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

Exit codes are a stable contract:

| Code | Meaning |
| --- | --- |
| `0` | the requested command completed |
| `1` | the command ran but the requested work failed |
| `2` | usage error (unknown command, bad or missing arguments) |

## Docker

```bash
docker build -t finepdf-to-images .
docker run --rm finepdf-to-images --help
```

The image contains no credentials. Pass a Hugging Face token at run time only when a command needs
one:

```bash
docker run --rm -e HF_TOKEN finepdf-to-images version
```

## Documentation

```bash
uv run --group docs mkdocs serve
```
