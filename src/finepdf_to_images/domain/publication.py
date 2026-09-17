"""The published dataset: its schema, its card, and what a publication would upload.

Pure. Assembling the rows, rendering the card and deciding which files a publication consists of
all happen here over plain values; talking to the Hub is the adapter's job. That split is what
makes "dry-run does not mutate the Hub" a structural fact rather than a promise -- the dry run
simply never reaches the adapter's write path.

The card is **generated**, not written. Its policy and vocabulary sections come from
:func:`policy_summary` and :func:`vocabulary_summary`, so the published description of the rules
cannot drift from the code that enforces them.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import canonical_bytes, content_digest, sha256_hex

#: Bumped when the published row shape changes. Consumers index on these names.
SCHEMA_VERSION = 1

DEFAULT_REPO = "NoeFlandre/finepdf-to-images-poc"

#: ``namespace/name`` as the Hub spells it. The destination is interpolated into API calls and
#: printed in the card, so it is validated rather than trusted -- the same rule SourceRef applies
#: to where the data comes *from*.
_REPO_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,94}/[A-Za-z0-9][A-Za-z0-9._-]{0,94}\Z")

#: Files a publication always writes, in upload order.
DOCUMENTS_FILE = "data/documents.jsonl"
IMAGES_FILE = "data/images.jsonl"
MANIFEST_FILE = "manifest.json"
CARD_FILE = "README.md"

#: The published row schema, as (field, description). Emitted into the card so the documentation
#: and the data are generated from one source.
DOCUMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("row_index", "position of the row in the source shard"),
    ("row_id", "FinePDFs row id, e.g. `<urn:uuid:...>`"),
    ("url", "source document URL as recorded by FinePDFs"),
    ("final_url", "URL the bytes were actually served from, after any redirect"),
    ("language", "language recorded by FinePDFs for the row"),
    ("relevant", "whether the agriculture scorer marked the row relevant"),
    ("relevance_score", "number of distinct concept groups matched"),
    ("matched_terms", "the exact vocabulary terms that produced the decision"),
    ("retrieved", "whether the source PDF was successfully retrieved"),
    ("failure_reason", "why retrieval produced no artifact, when it did not"),
    ("pdf_sha256", "SHA-256 of the retrieved PDF bytes"),
    ("pdf_bytes", "size of the retrieved PDF in bytes"),
    ("image_count", "number of images extracted from the PDF"),
    ("disposition", "what the publication policy permits for this artifact"),
    ("license_status", "what is known about the document's redistribution terms"),
)

IMAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("row_id", "FinePDFs row id of the document the image came from"),
    ("pdf_sha256", "SHA-256 of the PDF the image was extracted from"),
    ("page_index", "zero-based page the image appears on"),
    ("image_index", "zero-based position of the image on that page"),
    ("sha256", "SHA-256 of the extracted image bytes"),
    ("mime", "media type of the extracted image"),
    ("width", "decoded width in pixels"),
    ("height", "decoded height in pixels"),
    ("byte_size", "size of the extracted image in bytes"),
    ("duplicate_of", "reference to the first occurrence, when the bytes repeat"),
)


class PublicationError(ValueError):
    """Inputs that cannot be published as they stand."""


@dataclasses.dataclass(frozen=True, slots=True)
class PublishFile:
    """One file a publication would write, with the identity it will have."""

    path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)

    @property
    def size(self) -> int:
        return len(self.data)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclasses.dataclass(frozen=True, slots=True)
class PublicationPlan:
    """Everything a publication would do, computed without touching the Hub."""

    repo: str
    files: tuple[PublishFile, ...]
    manifest: Mapping[str, Any]

    @property
    def digest(self) -> str:
        """One identity for the whole publication, over every file's path and content."""
        return content_digest([file.as_dict() for file in self.files])

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "digest": self.digest,
            "files": [file.as_dict() for file in self.files],
            "counts": dict(self.manifest["counts"]),
        }


def _index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, Mapping[str, Any]]:
    return {row.get(key): row for row in rows}


def build_document_rows(
    *,
    scored: Sequence[Mapping[str, Any]],
    retrieved: Sequence[Mapping[str, Any]],
    extracted: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One published row per scored document, in shard order.

    Every scored row appears, not only the retrieved ones. A row that was judged irrelevant, or
    whose URL was refused, or whose server returned HTML, is part of the result: a dataset that
    silently drops its failures cannot be used to reproduce the run or to argue with the scorer.
    """
    by_retrieval = _index(retrieved, "row_id")
    by_extraction = _index(extracted, "row_id")

    rows = [
        _document_row(
            row,
            by_retrieval.get(row.get("row_id"), {}),
            by_extraction.get(row.get("row_id"), {}),
        )
        for row in scored
    ]
    return sorted(rows, key=lambda row: (row["row_index"] is None, row["row_index"]))


def _document_row(
    scored: Mapping[str, Any], retrieval: Mapping[str, Any], extraction: Mapping[str, Any]
) -> dict[str, Any]:
    """One published row, joining what each stage knows about the same document."""
    relevance = _section(scored, "relevance")
    publication = _section(retrieval, "publication")
    licence = _section(publication, "license")
    return {
        "row_index": scored.get("row_index"),
        "row_id": scored.get("row_id"),
        "url": scored.get("url"),
        "final_url": _text(retrieval, "final_url"),
        "language": _text(relevance, "language"),
        "relevant": bool(relevance.get("relevant")),
        "relevance_score": _number(relevance, "score"),
        "matched_terms": list(relevance.get("matched_terms") or ()),
        "retrieved": bool(retrieval.get("ok")),
        "failure_reason": _text(retrieval, "reason"),
        "pdf_sha256": _text(retrieval, "sha256"),
        "pdf_bytes": _number(retrieval, "byte_size"),
        "image_count": _number(extraction, "image_count"),
        "disposition": _text(publication, "disposition"),
        "license_status": _text(licence, "status"),
    }


def _section(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested block, defaulted to empty. Stage outputs omit what does not apply."""
    value = row.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value) if isinstance(value, str) else ""


def _number(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def build_image_rows(images: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The image index, reduced to the published fields and stably ordered."""
    rows = [
        {
            "row_id": image.get("document_row_id"),
            "pdf_sha256": image.get("pdf_sha256"),
            "page_index": image.get("page_index"),
            "image_index": image.get("image_index"),
            "sha256": image.get("sha256"),
            "mime": image.get("mime"),
            "width": image.get("width"),
            "height": image.get("height"),
            "byte_size": image.get("byte_size"),
            "duplicate_of": image.get("duplicate_of"),
        }
        for image in images
    ]
    return sorted(
        rows, key=lambda row: (str(row["pdf_sha256"]), row["page_index"], row["image_index"])
    )


def build_manifest(
    *,
    source: Mapping[str, Any],
    sampling: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    vocabulary_version: int,
    encoder: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The published run manifest.

    Carries no timestamp: two publications of the same pilot output must produce the same bytes,
    or the idempotency claim is decided by the clock rather than by the data.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "source": dict(source),
        "sampling": dict(sampling),
        "vocabulary_version": vocabulary_version,
        "encoder": dict(encoder or {}),
        "counts": _counts(documents, images),
        "publishes_source_bytes": any(
            row.get("disposition") == "publish-artifact" for row in documents
        ),
        "policy": policy.policy_summary(),
        "documents_digest": content_digest(list(documents)),
        "images_digest": content_digest(list(images)),
    }


def _counts(
    documents: Sequence[Mapping[str, Any]], images: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    return {
        "documents": len(documents),
        "relevant": sum(1 for row in documents if row["relevant"]),
        "retrieved": sum(1 for row in documents if row["retrieved"]),
        "images": len(images),
        "unique_images": len({row["sha256"] for row in images}),
    }


def build_plan(
    *,
    repo: str,
    manifest: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
) -> PublicationPlan:
    """Everything the publication consists of, as bytes, without touching the Hub."""
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        raise PublicationError(f"invalid destination repo {repo!r}: expected namespace/name")

    card = render_card(manifest)
    files = (
        PublishFile(CARD_FILE, card.encode("utf-8")),
        PublishFile(MANIFEST_FILE, canonical_bytes(manifest) + b"\n"),
        PublishFile(DOCUMENTS_FILE, _jsonl(documents)),
        PublishFile(IMAGES_FILE, _jsonl(images)),
    )
    return PublicationPlan(repo=repo, files=files, manifest=manifest)


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(dict(row)) + b"\n" for row in rows)


def is_noop(plan: PublicationPlan, remote: Mapping[str, str]) -> bool:
    """Whether every planned file is already on the Hub with exactly these bytes.

    ``remote`` maps path to SHA-256. Idempotency is decided by content, so re-running the same
    pilot is a no-op no matter how many times it happens.
    """
    return all(remote.get(file.path) == file.sha256 for file in plan.files)


def _table(fields: Sequence[tuple[str, str]]) -> str:
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in fields)
    return f"| field | meaning |\n| --- | --- |\n{rows}"


def render_card(manifest: Mapping[str, Any]) -> str:
    """The dataset card, generated from the manifest and the policy.

    Generated rather than written: the policy and vocabulary sections come from the same functions
    that enforce them, so the published description cannot drift from the behaviour. A card that
    disagrees with the code is worse than no card.
    """
    counts = manifest["counts"]
    source = manifest["source"]
    sampling = manifest["sampling"]
    seed = sampling["seed"]
    rules = manifest["policy"]
    vocabulary = vocabulary_summary()
    bytes_note = (
        "Some source bytes are republished; see the per-row `disposition`."
        if manifest["publishes_source_bytes"]
        else (
            "**No source PDF or image bytes are republished.** Every artifact resolved to "
            "`metadata-only`: the hash and provenance are here, the bytes are not."
        )
    )

    return f"""---
license: odc-by
task_categories:
- text-classification
language:
- en
tags:
- agriculture
- finepdfs
- proof-of-concept
pretty_name: FinePDFs agriculture pilot (metadata and hashes)
---

# finepdf-to-images — bounded agriculture pilot

A **proof of concept**, not a corpus. It samples one pinned shard of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), scores each row
for agriculture relevance from its extracted text, retrieves only the selected source PDFs,
extracts their embedded images, and publishes **this index**.

{bytes_note}

## Source

| | |
| --- | --- |
| dataset | [`{source["dataset"]}`](https://huggingface.co/datasets/{source["dataset"]}) |
| revision | `{source["revision"]}` |
| config / split / shard | `{source["config"]}` / `{source["split"]}` / `{source["shard"]}` |
| sampling | limit {sampling["limit"]}, strategy `{sampling["strategy"]}`, seed `{seed}` |

The shard holds 388,000 rows in 388 row groups. A run reads only the row groups its
limit requires — never the shard, never the corpus.

## Counts

| | |
| --- | --- |
| documents scored | {counts["documents"]} |
| judged relevant | {counts["relevant"]} |
| PDFs retrieved | {counts["retrieved"]} |
| image references | {counts["images"]} |
| unique images | {counts["unique_images"]} |

## Files

| path | contents |
| --- | --- |
| `{MANIFEST_FILE}` | run manifest: source, sampling, counts, policy, digests |
| `{DOCUMENTS_FILE}` | one row per scored document |
| `{IMAGES_FILE}` | one row per extracted image |

Digests: documents `{manifest["documents_digest"][:16]}…`, images
`{manifest["images_digest"][:16]}…`. Both are SHA-256 over the canonical JSON of the rows.

### `{DOCUMENTS_FILE}`

{_table(DOCUMENT_FIELDS)}

### `{IMAGES_FILE}`

{_table(IMAGE_FIELDS)}

## Relevance

A keyword filter over English text, vocabulary version {vocabulary["version"]}:
{vocabulary["concept_count"]} concepts in {len(vocabulary["groups"])} groups
({", ".join(sorted(vocabulary["groups"]))}), {vocabulary["surface_form_count"]} surface forms.
A document is relevant when it matches at least
{vocabulary["thresholds"]["min_groups"]} groups, or at least
{vocabulary["thresholds"]["min_concepts_in_one_group"]} distinct concepts in one group.

Every relevant row carries `matched_terms`, the exact terms that produced the decision, so you can
disagree with it. Terms deliberately excluded as ambiguous, with reasons, are listed in the
manifest under `policy` and in the repository.

This is not a multilingual classifier and not a model. It cannot recognise a document that never
uses the vocabulary, and it will flag one that mentions farming in passing.

## Licensing and redistribution

{rules["source_attribution"]}

{rules["limitations"]}

Default disposition: **`{rules["default_disposition"]}`**. Bytes are republished only for a
`declared-open` status carrying an allow-listed identifier *and* backed by a human decision
recorded in the source repository. This pilot ships no such entries.

Every row carries enough provenance — dataset, revision, config, split, shard, row index, row id
and URL — to trace it back to the exact FinePDFs row and source document.

## Takedown

{rules["takedown"]}

## Reproducing this

```bash
git clone https://github.com/NoeFlandre/finepdf-to-images
cd finepdf-to-images && uv sync --locked
uv run finepdf-to-images select --limit {sampling["limit"]} --out out/select
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
uv run finepdf-to-images retrieve --scored out/score/scored.jsonl \\
  --select-manifest out/select/manifest.json --relevant-only --out out/retrieve
uv run finepdf-to-images extract --retrieved out/retrieve/retrieved.jsonl \\
  --pdf-root out/retrieve --out out/extract
```

Selection, scoring and the manifests are byte-identical across runs. **Retrieval is not**: it
depends on what third-party servers return on the day, and a 2023 crawl's URLs decay. **Extracted
image bytes are not portable across machines** either — Pillow re-encodes to PNG and the deflate
implementation differs by wheel, so hashes differ between platforms. The manifest records the
encoder that produced this run.

## Limitations

- {counts["documents"]} documents from **one shard of one language config**. Not a sample of
  FinePDFs, and nothing here should be read as one.
- English vocabulary only.
- Embedded images only: no page rendering, no OCR, no layout inference.
- Retrieval failures are kept as rows with a `failure_reason`, because a dataset that drops its
  failures cannot be used to reproduce the run.
"""
