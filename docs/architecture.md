# Architecture

## Layering

```
cli / pipeline      composition roots
        |
   adapters         Hugging Face, HTTP, filesystem, PDF, publishing
        |
    domain          pure decisions: models, scoring, policy, hashing, serialization
```

Dependencies point **inwards only**. The domain never imports an adapter, the CLI, or the pipeline,
and never imports `httpx`, `pypdf`, `pyarrow`, `huggingface_hub`, `os`, `pathlib`, `subprocess`,
`tempfile`, `urllib`, `socket` or `shutil`.

This is not a convention, it is a test. `tests/architecture/test_import_boundaries.py` parses every
module in `src/` and fails the build on a violation or on an import cycle.

## Why

The interesting logic of this proof of concept — relevance scoring, publication policy, content
addressing, canonical serialization — is decision logic. Keeping it free of I/O is what makes the
run deterministic, cheap to property-test, and possible to replay from fixtures without a network
or a Hugging Face token.

## Determinism

Every stage is expected to be reproducible byte for byte from the same pinned inputs: stable
ordering, canonical JSON, SHA-256 content addressing, and an explicit sampling seed. See
[Quality gates](quality.md).
