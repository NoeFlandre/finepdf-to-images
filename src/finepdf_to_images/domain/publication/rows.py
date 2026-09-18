"""Turn a run's stage output into the rows that get published.

Document rows, image rows and the dataset rows themselves. Everything here is a pure function
from stage output to plain values: what reaches the Hub is decided in :mod:`plan`, and moving
the bytes is the adapter's job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication.fields import (
    _flag,
    _index,
    _items,
    _number,
    _section,
    _text,
)
from finepdf_to_images.domain.publication.schema import _DIGEST_RE, PublicationError
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.serialization import sha256_hex


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
    text = _text(scored, "text")
    actual_sha256 = sha256_hex(text.encode("utf-8"))
    recorded_sha256 = scored.get("text_sha256")
    if recorded_sha256 is not None and recorded_sha256 != actual_sha256:
        raise PublicationError(
            f"text_sha256 mismatch for row {scored.get('row_id')!r}: "
            f"recorded {recorded_sha256!r} != computed {actual_sha256!r}"
        )
    return {
        # row_index is genuinely nullable -- a row the shard reader could not position -- and the
        # sort below relies on that, so it is read directly rather than through the guards.
        "row_index": scored.get("row_index"),
        "row_id": _text(scored, "row_id"),
        "url": _text(scored, "url"),
        "final_url": _text(retrieval, "final_url"),
        "language": _text(relevance, "language"),
        "text": text,
        "text_sha256": actual_sha256,
        "relevant": _flag(relevance, "relevant"),
        "relevance_score": _number(relevance, "score"),
        "matched_terms": _items(relevance, "matched_terms"),
        "retrieved": _flag(retrieval, "ok"),
        "failure_reason": _text(retrieval, "reason"),
        "pdf_sha256": _text(retrieval, "sha256"),
        "pdf_bytes": _number(retrieval, "byte_size"),
        "image_count": _number(extraction, "image_count"),
        "pdf": _published_pdf_path(publication, _text(retrieval, "sha256")),
        "disposition": _text(publication, "disposition"),
        "license_status": _text(licence, "status"),
    }


def _published_pdf_path(publication: Mapping[str, Any], digest: str) -> str | None:
    """Where this row's PDF is published, or ``None`` when only its metadata is.

    Derived from the disposition the policy already recorded rather than taken as an argument:
    the column then cannot disagree with the decision it reports, and a row whose bytes were never
    cleared has no path to point at.
    """
    if publication.get("disposition") != str(policy.Disposition.PUBLISH_ARTIFACT):
        return None
    return artifact_path(digest) if _DIGEST_RE.match(digest) else None


def derive_relevant_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents judged relevant by the scorer, in shard order."""
    return [dict(row) for row in documents if bool(row.get("relevant"))]


def derive_retrieved_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents whose source PDF was successfully retrieved, in shard order."""
    return [dict(row) for row in documents if bool(row.get("retrieved"))]


def cleared_pdf_digests(documents: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Digest -> row id, for the PDFs of cleared rows that actually have one."""
    return {
        str(row["pdf_sha256"]): str(row.get("row_id"))
        for row in documents
        if row.get("disposition") == str(policy.Disposition.PUBLISH_ARTIFACT)
        and _DIGEST_RE.match(str(row.get("pdf_sha256") or ""))
    }


def build_image_rows(
    images: Sequence[Mapping[str, Any]], published: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """The image index, reduced to the published fields and stably ordered.

    ``published`` maps the digests whose bytes ship to their media type. A row for a digest that
    is not in it gets ``image: None`` rather than being dropped: the dataset should say what it
    declined to publish, not hide it.
    """
    shipped = dict(published or {})
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
            "image": (
                image_path(str(image.get("sha256")), shipped[str(image.get("sha256"))])
                if str(image.get("sha256")) in shipped
                else None
            ),
        }
        for image in images
    ]
    return sorted(
        rows, key=lambda row: (str(row["pdf_sha256"]), row["page_index"], row["image_index"])
    )


def build_dataset_rows(
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    image_bytes: Mapping[str, bytes] | None = None,
) -> list[dict[str, Any]]:
    """One row per **relevant** document: the published dataset, as plain values.

    Only relevant rows. The 948 rejected documents in the pilot carry no text worth reading and no
    images; publishing them made the dataset look like a pipeline log rather than a corpus. What
    the scorer declined is a fact about the run, and the run's manifest is where it belongs.

    ``matched_terms`` earns its column where the other bookkeeping did not: it is the *reason the
    row exists*. A relevance score of ``3`` communicates nothing on its own, but `soil`,
    `irrigation`, `crop rotation` lets a reader argue with the selection instead of taking it on
    faith.

    **One row per image, not per document.** A list-of-images column is typed correctly by
    `datasets-server` but the viewer renders it as JSON rather than as pictures, so the dataset's
    whole point was invisible to anyone who had not written code against it. A scalar ``image``
    column renders as a thumbnail. Repeating a document's text across its images costs almost
    nothing once parquet dictionary-compresses it -- 31 MB of logical duplication came to about
    90 KB on the pilot.

    **A document with no image contributes no rows.** This is `finepdf-to-images`: every published
    row carries a picture, by construction rather than by filter.

    ``image_bytes`` maps a digest to the bytes to embed. A digest missing from it cannot be
    embedded, and a document left with nothing embeddable simply produces no rows rather than
    shipping a broken reference.
    """
    by_row = _images_by_row(images)
    available = dict(image_bytes or {})
    rows: list[dict[str, Any]] = []
    for document in derive_relevant_rows(documents):
        embedded = _embedded_images(by_row.get(str(document.get("row_id")), ()), available)
        terms = [str(term) for term in document.get("matched_terms") or ()]
        rows.extend(
            {
                "pdf_url": _text(document, "url"),
                "image": image,
                "text": _text(document, "text"),
                "matched_terms": terms,
            }
            for image in embedded
        )
    return rows


def _images_by_row(images: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """The extraction index grouped by owning document, each group in page order.

    Reads ``document_row_id``, the raw extraction key -- the published rows rename it to
    ``row_id``, and conflating the two silently yields no images at all.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for image in images:
        grouped.setdefault(str(image.get("document_row_id")), []).append(image)
    for group in grouped.values():
        group.sort(key=lambda image: (_number(image, "page_index"), _number(image, "image_index")))
    return grouped


def _embedded_images(
    images: Sequence[Mapping[str, Any]], available: Mapping[str, bytes]
) -> list[dict[str, Any]]:
    """The embeddable images for one document, each digest once, in first-occurrence order.

    The same bytes can appear on several pages, and the old index emitted a row per occurrence
    with ``duplicate_of`` pointing back at the first. Embedding repeats that way would hand a
    reader the same picture several times, so a digest is carried once.

    ``{"bytes": ..., "path": ...}`` is the shape the Hub's ``Image`` feature decodes; the path is
    a label the viewer shows, not a file that has to exist in the repository.
    """
    embedded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in images:
        digest = str(image.get("sha256") or "")
        if digest in seen or digest not in available:
            continue
        seen.add(digest)
        embedded.append(
            {"bytes": available[digest], "path": image_path(digest, str(image.get("mime") or ""))}
        )
    return embedded


def all_image_digests(images: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Digest -> mime for **every** extracted image, cleared or not.

    The publication policy gates which *PDF* bytes may be redistributed. It no longer gates the
    images: the dataset owner decided that this proof of concept publishes every image it
    extracts, accepting that most sources carry no declared licence. The card states that plainly
    and carries the takedown route, which is the obligation that replaces the filter.

    Keyed by digest because the same image can appear on several pages and in several documents;
    it is embedded once per document that references it.
    """
    return {
        digest: str(image.get("mime") or "")
        for image in images
        if _DIGEST_RE.match(digest := str(image.get("sha256") or ""))
    }
