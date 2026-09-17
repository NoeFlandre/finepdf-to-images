"""The minimal published dataset: one table, four columns, relevant rows only.

Pure. Takes plain values and returns plain values; writing the parquet and talking to the Hub are
the adapters' job.

This module exists because the first published shape was built for the pipeline rather than for a
reader: 203 files, 18 columns of stage bookkeeping, 948 of 1000 rows being documents the scorer
*rejected*, and images as loose digest-named files that nothing in the viewer linked back to the
document they came from. Reading it meant joining two JSONL files on ``row_id`` and fetching each
image by hand.

So the row here carries only what a reader needs: the source URL, the text, the images embedded in
the row so the viewer renders them inline, and the terms that made the document relevant.
``matched_terms`` is the one piece of pipeline state that earns its place -- it is the *reason the
row exists*, and a reader can argue with ``soil`` and ``irrigation`` where a score of ``3`` tells
them nothing.

The card is **generated**, not written, and takes its attribution and takedown strings from
:mod:`finepdf_to_images.domain.policy`, so the published statement of an obligation cannot drift
from the module that defines it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.publication import PublicationPlan, PublishFile, validate_repo

#: The single parquet the dataset consists of. The name follows the Hub's shard convention so the
#: viewer and ``load_dataset`` recognise it without extra configuration.
DATASET_FILE = "data/train-00000-of-00001.parquet"

CARD_FILE = "README.md"

CONFIG_NAME = "default"
SPLIT_NAME = "train"

#: The published columns, each with the feature type the card declares and the meaning the card's
#: schema table prints. Both the front matter and the table are generated from this one tuple, so a
#: column cannot be added to the rows and forgotten in the machine-read contract -- a mismatch
#: there makes the viewer fail outright rather than degrade.
DATASET_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("pdf_url", "dtype", "string", "URL of the source PDF"),
    ("text", "dtype", "string", "text extracted from the source document"),
    ("images", "sequence", "image", "images embedded from that PDF, in page order"),
    ("matched_terms", "sequence", "string", "terms that made the document relevant"),
)


def build_dataset_rows(
    *,
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    image_bytes: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    """The published rows: one per relevant document, in the order ``documents`` arrives.

    ``documents`` is expected in shard order (that is how :func:`publication.build_document_rows`
    returns them) and that order is preserved rather than recomputed, because the minimal row no
    longer carries ``row_index`` to sort on.

    ``image_bytes`` maps a digest to the bytes that may be republished. It is a separate argument
    because the image index records every extracted image, while only images from allow-listed
    sources may be redistributed -- for most documents the mapping is empty, and a digest absent
    from it simply contributes no image. That is the ordinary case, not an error: the row then
    carries ``[]`` rather than a reference to something the reader cannot fetch.

    Images within a row are ordered by ``(page_index, image_index)``, and the whole function is
    deterministic in its inputs. That is load-bearing rather than tidiness: republishing identical
    input must produce identical bytes, or "a second apply is a no-op" becomes a claim decided by
    dict iteration order.
    """
    by_row = _by_row(images)
    return [
        _row(document, by_row.get(document.get("row_id"), ()), image_bytes)
        for document in documents
        if document.get("relevant")
    ]


def _row(
    document: Mapping[str, Any],
    images: Sequence[Mapping[str, Any]],
    image_bytes: Mapping[str, bytes],
) -> dict[str, Any]:
    """One published row: the four columns a reader needs, and nothing the pipeline needed."""
    return {
        "pdf_url": str(document.get("url") or ""),
        "text": str(document.get("text") or ""),
        "images": _embedded(images, image_bytes),
        "matched_terms": [str(term) for term in document.get("matched_terms") or ()],
    }


def _by_row(images: Sequence[Mapping[str, Any]]) -> dict[Any, list[Mapping[str, Any]]]:
    """The image index grouped by the document each image came from."""
    grouped: dict[Any, list[Mapping[str, Any]]] = {}
    for image in images:
        grouped.setdefault(image.get("row_id"), []).append(image)
    return grouped


def _embedded(
    images: Sequence[Mapping[str, Any]], image_bytes: Mapping[str, bytes]
) -> list[dict[str, Any]]:
    """One document's publishable images, in page order, as the embedded-image feature wants them.

    ``path: None`` is what tells the reader the bytes are the image: a non-null path makes the
    viewer chase a file that this dataset deliberately no longer ships.
    """
    ordered = sorted(
        images, key=lambda image: (_order(image, "page_index"), _order(image, "image_index"))
    )
    return [
        {"bytes": image_bytes[digest], "path": None}
        for image in ordered
        if (digest := str(image.get("sha256") or "")) in image_bytes
    ]


def _order(image: Mapping[str, Any], key: str) -> int:
    """A sort key that tolerates a missing index instead of raising mid-sort.

    An image row with no ``page_index`` is a bug upstream, but failing the whole publication on it
    would be a worse answer than sorting it first and letting the row be inspected.
    """
    value = image.get(key)
    return value if isinstance(value, int) else -1


def _front_matter_features() -> str:
    """The ``features`` block, generated from :data:`DATASET_FIELDS`.

    ``sequence:`` rather than ``list:``: both parse, but ``sequence`` is the spelling the older
    ``datasets`` versions running on the Hub understand. Without the ``image`` feature the viewer
    shows a path string where the picture should be.
    """
    return "\n".join(
        f"    - name: {name}\n      {kind}: {value}" for name, kind, value, _ in DATASET_FIELDS
    )


def _schema_table() -> str:
    """The reader-facing schema table, generated from the same tuple as the front matter."""
    rows = "\n".join(
        f"| `{name}` | `{value if kind == 'dtype' else f'sequence[{value}]'}` | {meaning} |"
        for name, kind, value, meaning in DATASET_FIELDS
    )
    return f"| column | type | meaning |\n| --- | --- | --- |\n{rows}"


def build_plan(*, repo: str, card: str, table: bytes) -> PublicationPlan:
    """The whole publication: a card and one parquet file.

    Two files, where the previous layout published six plus 193 loose artifacts. The images are
    embedded in the table rather than uploaded beside it, so there is nothing to join and nothing
    to fetch separately -- which is what made the old layout unusable in the viewer.

    Anything else already on the Hub is not named here, and a publication removes what it does not
    contain (ADR-0014). That is how the old tree is cleaned up: not by a migration script, but by
    publishing the dataset as it should be.
    """
    validate_repo(repo)
    return PublicationPlan(
        repo=repo,
        files=(
            PublishFile(CARD_FILE, card.encode("utf-8")),
            PublishFile(DATASET_FILE, table),
        ),
        manifest={},
    )


def render_card(*, repo: str, source: Mapping[str, Any], sampling: Mapping[str, Any]) -> str:
    """The dataset card: front matter, one sentence, the schema, provenance, licensing, a snippet.

    Generated rather than hand-written so it cannot drift from the code, and deliberately short:
    the card it replaces had grown to 224 lines of this project explaining itself to its own
    maintainers -- the scoring vocabulary, the policy summary, the allow list with statutory
    citations, notes on PNG encoders. All of that lives in the repository's own docs. What is left
    is what a reader needs: what this is, what a row holds, where it came from, what they may do
    with it.

    The front matter is a machine-read contract rather than prose: it is what makes the viewer
    render the images inline and ``load_dataset`` return PIL images with no second fetch.
    """
    return f"""---
configs:
  - config_name: {CONFIG_NAME}
    data_files:
      - split: {SPLIT_NAME}
        path: {DATASET_FILE}
dataset_info:
  features:
{_front_matter_features()}
license: odc-by
---

Documents sampled from one pinned shard of
[{source["dataset"]}](https://huggingface.co/datasets/{source["dataset"]}), kept when their
extracted text matched an agriculture vocabulary, with the images embedded from each source PDF.

{_schema_table()}

## Source

| | |
| --- | --- |
| dataset | `{source["dataset"]}` |
| revision | `{source["revision"]}` |
| config / split / shard | `{source["config"]}` / `{source["split"]}` / `{source["shard"]}` |
| sampling | limit {sampling["limit"]}, strategy `{sampling["strategy"]}` |

## Licensing

{policy.SOURCE_ATTRIBUTION}
Image bytes are included only for cleared sources; every other row carries an empty `images` list.
{policy.TAKEDOWN_CONTACT}

```python
from datasets import load_dataset
dataset = load_dataset("{repo}", split="{SPLIT_NAME}")
```
"""
