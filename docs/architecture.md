# Architecture

## Layering

```
cli / pipeline      composition roots
        |
   adapters         Hugging Face, HTTP, filesystem, PDF, publishing
        |
    domain          pure decisions: models, scoring, policy, hashing, serialization
```

Dependencies point **inwards only**. The domain never imports an adapter, the CLI, or the pipeline. The domain never imports `httpx`, `pypdf`, `pyarrow`, `huggingface_hub`, `os`, `pathlib`, `subprocess`, `tempfile`, `urllib`, `socket` or `shutil`.

This rule is a test, not a convention. The test in `tests/architecture/` parses each module in `src/`. The build fails when a module breaks the rule or when an import cycle occurs.

The test finds each import form that can cross a layer:

- `import finepdf_to_images.adapters`
- `from finepdf_to_images.adapters import x`
- `from finepdf_to_images import adapters`
- relative imports

The analyser has its own regression tests in `test_boundaries_selfcheck.py`. A boundary check that silently matches nothing gives false confidence.

## Reason for this design

The main logic of this proof of concept is decision logic. It includes relevance scoring, publication policy, content addressing, and canonical serialization. The domain has no I/O. This makes each run deterministic. It also makes property tests cheap. You can replay a run from fixtures without a network or a Hugging Face token.

## Determinism

Each stage must give the same bytes from the same pinned inputs. The pipeline uses these methods:

- stable ordering
- canonical JSON
- SHA-256 content addressing
- an explicit sampling seed

For more information, see [Quality gates](quality.md).

**There is one exception.** The bytes of the extracted *images* are not the same on all machines. Pillow re-encodes the images to PNG. The PNG encoding depends on the deflate implementation that the installed wheel links to. The pixels are the same, but the bytes, the `sha256`, and the path are different. All other stages give the same bytes. See [TD-007](technical-debt.md).
