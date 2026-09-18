"""Extracting and indexing the images inside retrieved PDFs."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.images import ImageExtractor, encoder_versions
from finepdf_to_images.adapters.storage import read_bytes, write_bytes
from finepdf_to_images.domain.images import (
    ImageExtractionError,
    ImageRecord,
    build_image_record,
    is_publishable_size,
    sort_key,
)
from finepdf_to_images.domain.retrieval import (
    artifact_path,
)
from finepdf_to_images.domain.serialization import (
    canonical_bytes,
    canonical_jsonl,
    content_digest,
    sha256_hex,
)
from finepdf_to_images.pipeline.shared import (
    DOCUMENTS_NAME,
    IMAGES_NAME,
    MANIFEST_NAME,
    _identity_of,
)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    manifest: dict[str, Any]
    manifest_path: pathlib.Path
    images_path: pathlib.Path
    documents_path: pathlib.Path
    documents: int
    with_images: int
    images: int
    unique_images: int
    failed: int


def _pdf_bytes_for(record: Mapping[str, Any], pdf_root: pathlib.Path) -> bytes:
    """Read one retrieved PDF, refusing anything that is not where the retrieve stage put it.

    The stored path must be exactly the content-addressed path the digest implies. Without that
    check a hand-edited retrieved.jsonl could name ``../secret.pdf`` or an absolute path -- and
    ``pathlib`` discards the root entirely when a part is absolute -- so the stage would read
    arbitrary files and publish the images inside them. Every other stage validates its input
    reference; this one was the exception.

    The bytes are then verified against the digest, so a corrupted or swapped artifact is caught
    rather than silently indexed under the wrong identity.
    """
    digest, expected = _checked_reference(record)
    try:
        data = read_bytes(pdf_root / expected)
    except OSError as error:
        # Re-raised without the absolute path: it would be published verbatim in documents.jsonl
        # and folded into documents_digest, making the digest depend on where the run happened and
        # leaking the operator's filesystem layout.
        raise ImageExtractionError(f"{type(error).__name__} reading {expected}") from error
    actual = sha256_hex(data)
    if actual != digest:
        raise ImageExtractionError(f"stored pdf hashes to {actual}, not the recorded {digest}")
    return data


def _publishable(extracted: Sequence[Any]) -> list[Any]:
    """Drop the page rules a PDF embeds as images. See ADR-0017."""
    return [image for image in extracted if is_publishable_size(image.width, image.height)]


def _image_record(image: Any, row_id: str, row_index: int, pdf_sha256: str) -> ImageRecord:
    return build_image_record(
        document_row_id=row_id,
        document_row_index=row_index,
        pdf_sha256=pdf_sha256,
        page_index=image.page_index,
        image_index=image.image_index,
        data=image.data,
        mime=image.mime,
        width=image.width,
        height=image.height,
        caption=getattr(image, "caption", ""),
        page_width=getattr(image, "page_width", 0.0),
        page_height=getattr(image, "page_height", 0.0),
    )


def _checked_reference(record: Mapping[str, Any]) -> tuple[str, str]:
    """The digest and the only path it may legitimately live at."""
    digest = record.get("sha256")
    stored = record.get("path")
    if not isinstance(digest, str) or not isinstance(stored, str):
        raise ImageExtractionError("record has no usable sha256 and path")
    expected = artifact_path(digest)
    if stored != expected:
        raise ImageExtractionError(f"path {stored!r} is not the content-addressed {expected!r}")
    return digest, expected


def _extract_one(
    *,
    extractor: ImageExtractor,
    record: Mapping[str, Any],
    pdf_root: pathlib.Path,
) -> tuple[list[ImageRecord], dict[tuple[int, int], bytes], str]:
    """Extract one document's images, or return the reason it could not be done.

    Returns the records, the raw bytes keyed by position, and the failure reason. The bytes are
    returned alongside rather than threaded through the records, which would put megabytes of
    image data into every manifest row -- and re-extracting per image would re-parse the whole
    document once per picture.
    """
    pdf_sha256 = str(record.get("sha256") or "")

    try:
        # Inside the try: `int(...)` on a non-numeric row index raises, and hoisting this out let
        # that escape run_extract and lose every document already extracted. The comment below has
        # claimed otherwise since before it was true.
        row_index, row_id, _url = _identity_of(record)
        pdf_bytes = _pdf_bytes_for(record, pdf_root)
        extracted = _publishable(extractor.extract(pdf_bytes))
        records = [_image_record(i, row_id, row_index, pdf_sha256) for i in extracted]
    except Exception as error:
        # Deliberately one handler. A malformed document, a missing file, a non-string path, a
        # non-numeric row index, or a third-party extractor raising something of its own are all
        # the same thing here: this document contributes nothing and the run continues. These used
        # to escape run_extract, and because the manifest is written after the loop, one bad row
        # lost every document already extracted.
        return [], {}, _reason_for(error)
    return records, {(i.page_index, i.image_index): i.data for i in extracted}, ""


def _reason_for(error: Exception) -> str:
    """Our own diagnostics read as themselves; anything else is named by its type."""
    return (
        str(error)
        if isinstance(error, ImageExtractionError)
        else f"{type(error).__name__}: {error}"
    )


def run_extract(
    *,
    extractor: ImageExtractor,
    records: Sequence[Mapping[str, Any]],
    pdf_root: pathlib.Path,
    out_dir: pathlib.Path,
) -> ExtractionResult:
    """Extract and index the images in every successfully retrieved PDF.

    Only records the retrieval stage marked ``ok`` are considered; a document that was never
    fetched has nothing to extract. A PDF with no embedded images is an explicit zero-image
    success, not a failure -- most PDFs on the open web genuinely contain none.

    Images are deduplicated by content across the whole run, so the same logo on forty pages is one
    artifact with forty references. Which occurrence is recorded as the original depends on the
    order of ``records``; the artifact itself is content-addressed, so only the ``duplicate_of``
    pointer moves.
    """
    image_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    failed = 0
    with_images = 0

    for record in records:
        if not record.get("ok") or not record.get("path"):
            continue
        images, payloads, error = _extract_one(
            extractor=extractor, record=record, pdf_root=pdf_root
        )

        document_rows.append(_document_row(record, images, error))
        if error:
            failed += 1
            continue
        with_images += 1 if images else 0

        image_rows.extend(_indexed(images, payloads, seen, out_dir))

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "stage": "extract",
        # The image bytes are not portable across Pillow builds, so a run has to say what
        # produced it. See TD-007.
        "encoder": encoder_versions(),
        "counts": {
            "documents": len(document_rows),
            "documents_with_images": with_images,
            "documents_failed": failed,
            "images": len(image_rows),
            "unique_images": len(seen),
        },
        "images_digest": content_digest(image_rows),
        "documents_digest": content_digest(document_rows),
    }
    images_path = write_bytes(out_dir / IMAGES_NAME, canonical_jsonl(image_rows))
    documents_path = write_bytes(out_dir / DOCUMENTS_NAME, canonical_jsonl(document_rows))
    manifest_path = write_bytes(out_dir / MANIFEST_NAME, canonical_bytes(manifest) + b"\n")
    return ExtractionResult(
        manifest=manifest,
        manifest_path=manifest_path,
        images_path=images_path,
        documents_path=documents_path,
        documents=len(document_rows),
        with_images=with_images,
        images=len(image_rows),
        unique_images=len(seen),
        failed=failed,
    )


def _document_row(
    record: Mapping[str, Any], images: Sequence[ImageRecord], error: str
) -> dict[str, Any]:
    """One row per retrieved document, whether or not it yielded images."""
    return {
        "row_index": record.get("row_index"),
        "row_id": record.get("row_id"),
        "url": record.get("url"),
        "pdf_sha256": record.get("sha256"),
        "pdf_path": record.get("path"),
        "image_count": len(images),
        "ok": not error,
        "error": error,
    }


def _indexed(
    images: Sequence[ImageRecord],
    payloads: Mapping[tuple[int, int], bytes],
    seen: dict[str, str],
    out_dir: pathlib.Path,
) -> list[dict[str, Any]]:
    """Write each new image once and return its manifest row, in document order.

    ``seen`` is keyed on the PDF's digest rather than its row id: a row id can be empty or
    repeated across documents, and then the ``duplicate_of`` reference points at nothing
    resolvable.
    """
    rows: list[dict[str, Any]] = []
    for image in sorted(images, key=sort_key):
        entry = image.as_dict()
        if image.sha256 in seen:
            entry["duplicate_of"] = seen[image.sha256]
        else:
            seen[image.sha256] = f"{image.pdf_sha256}#{image.page_index}.{image.image_index}"
            write_bytes(out_dir / image.path, payloads[(image.page_index, image.image_index)])
        rows.append(entry)
    return rows
