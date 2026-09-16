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

This is not a convention, it is a test. `tests/architecture/` parses every module in `src/` and
fails the build on a violation or on an import cycle. It understands every import form that can
cross a layer — `import finepdf_to_images.adapters`, `from finepdf_to_images.adapters import x`,
`from finepdf_to_images import adapters`, and relative imports — and the analyser itself carries
regression tests in `test_boundaries_selfcheck.py`, because a boundary check that silently matches
nothing buys confidence without paying for it.

## Why

The interesting logic of this proof of concept — relevance scoring, publication policy, content
addressing, canonical serialization — is decision logic. Keeping it free of I/O is what makes the
run deterministic, cheap to property-test, and possible to replay from fixtures without a network
or a Hugging Face token.

## Determinism

Every stage is expected to be reproducible byte for byte from the same pinned inputs: stable
ordering, canonical JSON, SHA-256 content addressing, and an explicit sampling seed. See
[Quality gates](quality.md).

**One exception, and it is worth knowing about.** Extracted *image* bytes are not portable across
machines: Pillow re-encodes them to PNG, and PNG encoding depends on which deflate implementation
the installed wheel links. Same pixels, different bytes, different `sha256`, different path. Every
other stage is byte-identical. See [TD-007](technical-debt.md).
