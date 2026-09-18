# Architecture decision records

Short records of decisions that would otherwise be re-litigated. One file per decision, numbered,
never rewritten in place — a decision that changes gets a new record that supersedes the old one.

Template: **Context** (the forces), **Decision** (what we chose), **Consequences** (what this costs
us, including what it makes harder).

- [ADR-0001 — Pure domain, thin adapters](0001-pure-domain-thin-adapters.md)
- [ADR-0002 — uv, Ruff, ty and a committed lockfile](0002-uv-ruff-ty-toolchain.md)
- [ADR-0003 — A dependency-free argparse CLI](0003-argparse-cli.md)
- [ADR-0004 — Pinned tiny-shard input](0004-pinned-tiny-shard-input.md)
- [ADR-0005 — Conservative publication policy](0005-conservative-publication-policy.md)
- [ADR-0006 — Refuse then fetch](0006-refuse-then-fetch.md)
- [ADR-0007 — Embedded images only](0007-embedded-images-only.md)
- [ADR-0008 — Quality gauntlet](0008-quality-gauntlet.md)
- [ADR-0009 — Publish an index, not a document dump](0009-publish-metadata-not-documents.md)
- [ADR-0010 — Declare explicit dataset configs in the card front matter](0010-dataset-viewer-configs.md)
- [ADR-0011 — Publish extracted document text under ODC-BY](0011-publish-extracted-text.md)
- [ADR-0012 — Derived relevant and retrieved splits for documents](0012-relevant-retrieved-splits.md)
- [ADR-0013 — Publish artifact bytes for allow-listed sources](0013-publish-allow-listed-artifact-bytes.md)
- [ADR-0014 — A publication removes what it does not contain](0014-a-publication-removes-what-it-does-not-contain.md)
- [ADR-0015 — Publish only rows that carry an image](0015-publish-only-rows-that-carry-an-image.md)
- [ADR-0016 — One row per image](0016-one-row-per-image.md)
- [ADR-0017 — Discard extracted images below 32px on a side](0017-discard-images-below-32px.md)
- [ADR-0018 — Drop images that appear on many pages](0018-drop-images-that-appear-on-many-pages.md)
- [ADR-0019 — Every published row is an image–caption pair](0019-every-row-is-an-image-caption-pair.md)
- [ADR-0020 — A scanned document publishes no figures](0020-a-scanned-document-has-no-figures.md)
- [ADR-0021 — Publish photographs, not line art](0021-publish-photographs-not-line-art.md)
