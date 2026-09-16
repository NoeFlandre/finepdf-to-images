"""Composition root: wires adapters into the pure domain.

Every function here is thin on purpose. If a decision looks interesting enough to argue about, it
belongs in :mod:`finepdf_to_images.domain`, where it can be tested without I/O.
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.images import ImageExtractor
from finepdf_to_images.adapters.retrieval import Transport, TransportError
from finepdf_to_images.adapters.source import ShardReader
from finepdf_to_images.adapters.storage import read_bytes, write_bytes
from finepdf_to_images.domain import policy
from finepdf_to_images.domain.images import (
    ImageExtractionError,
    ImageRecord,
    build_image_record,
    sort_key,
)
from finepdf_to_images.domain.retrieval import (
    FailureReason,
    RetrievalLimits,
    RetrievalRecord,
    UnsafeUrlError,
    artifact_path,
    validate_url,
)
from finepdf_to_images.domain.retrieval import evaluate as evaluate_response
from finepdf_to_images.domain.retrieval import failure as retrieval_failure
from finepdf_to_images.domain.scoring import score as score_text
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import (
    canonical_bytes,
    canonical_jsonl,
    content_digest,
    sha256_hex,
)
from finepdf_to_images.domain.source import (
    SELECTED_COLUMNS,
    SamplingSpec,
    SourceRef,
    build_manifest,
    build_record,
    read_window,
    select,
)

MANIFEST_NAME = "manifest.json"
RECORDS_NAME = "records.jsonl"
SCORED_NAME = "scored.jsonl"
RETRIEVED_NAME = "retrieved.jsonl"
IMAGES_NAME = "images.jsonl"
DOCUMENTS_NAME = "documents.jsonl"


@dataclass(frozen=True, slots=True)
class SelectionResult:
    manifest: dict[str, Any]
    manifest_path: pathlib.Path
    records_path: pathlib.Path
    selected: int
    rows_fetched: int
    row_groups_read: int


def run_select(
    *,
    reader: ShardReader,
    ref: SourceRef,
    spec: SamplingSpec,
    out_dir: pathlib.Path,
) -> SelectionResult:
    """Select a bounded sample from one pinned shard and write it deterministically."""
    max_rows = read_window(spec.limit, spec.strategy)
    window = reader.read(ref, max_rows=max_rows, columns=SELECTED_COLUMNS)
    records = [build_record(index, row) for index, row in enumerate(window.rows)]
    selected = select(records, spec)

    manifest = build_manifest(
        ref=ref,
        spec=spec,
        records=selected,
        read={
            "max_rows_requested": max_rows,
            "rows_fetched": window.rows_fetched,
            "row_groups_read": window.row_groups_read,
            "rows_considered": len(window.rows),
            "rows_per_row_group": window.rows_per_row_group,
            "shard_total_rows": window.total_rows,
            "shard_total_row_groups": window.total_row_groups,
        },
    )
    # Records first: the manifest indexes them, so a failure between the two writes must not leave
    # a manifest describing a file that does not exist.
    records_path = write_bytes(
        out_dir / RECORDS_NAME, canonical_jsonl([record.as_dict() for record in selected])
    )
    manifest_path = write_bytes(out_dir / MANIFEST_NAME, canonical_bytes(manifest) + b"\n")
    return SelectionResult(
        manifest=manifest,
        manifest_path=manifest_path,
        records_path=records_path,
        selected=len(selected),
        rows_fetched=window.rows_fetched,
        row_groups_read=window.row_groups_read,
    )


@dataclass(frozen=True, slots=True)
class ScoringResult:
    manifest: dict[str, Any]
    manifest_path: pathlib.Path
    scored_path: pathlib.Path
    scored: int
    relevant: int


def _as_text(value: Any, field: str, position: int) -> str:
    """Coerce a record field to text, refusing a type that is not one.

    ``or ""`` alone only defends against falsy values: a row with ``{"text": 123}`` reached the
    normalizer and raised a TypeError outside the CLI's handlers, so the user saw a traceback
    instead of a diagnostic. A non-string here means the input file is not what it claims to be.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"record {position}: expected {field} to be a string, got {type(value).__name__}"
        )
    return value


def run_score(*, records: Sequence[Mapping[str, Any]], out_dir: pathlib.Path) -> ScoringResult:
    """Score already-selected records for agriculture relevance.

    Takes the records rather than a path: reading them is the caller's business, and keeping this
    function over plain data is what lets the whole stage be tested without a filesystem.
    """
    rows: list[dict[str, Any]] = []
    relevant = 0
    for position, record in enumerate(records):
        result = score_text(
            _as_text(record.get("text"), "text", position),
            language=_as_text(record.get("language"), "language", position),
        )
        relevant += int(result.relevant)
        rows.append(
            {
                "row_index": record.get("row_index"),
                "row_id": record.get("row_id"),
                "url": record.get("url"),
                "relevance": result.as_dict(),
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "stage": "score",
        "vocabulary": vocabulary_summary(),
        "counts": {"scored": len(rows), "relevant": relevant},
        # Deliberately computed over the records *without* their text, because that is the exact
        # shape the select stage publishes as records_digest. Digesting a different shape would
        # produce a number that matches nothing upstream -- a provenance link in name only.
        "input_digest": content_digest(
            [{key: value for key, value in record.items() if key != "text"} for record in records]
        ),
        "scored_digest": content_digest(rows),
    }
    scored_path = write_bytes(out_dir / SCORED_NAME, canonical_jsonl(rows))
    manifest_path = write_bytes(out_dir / MANIFEST_NAME, canonical_bytes(manifest) + b"\n")
    return ScoringResult(
        manifest=manifest,
        manifest_path=manifest_path,
        scored_path=scored_path,
        scored=len(rows),
        relevant=relevant,
    )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    manifest: dict[str, Any]
    manifest_path: pathlib.Path
    records_path: pathlib.Path
    attempted: int
    retrieved: int
    unique: int
    failed: int


def _fetch_one(
    *,
    transport: Transport,
    limits: RetrievalLimits,
    row: Mapping[str, Any],
) -> tuple[RetrievalRecord, bytes]:
    """Retrieve one row's document, converting every failure into a record rather than an error.

    Returns the record and the bytes, so the caller can store them exactly once per digest.
    """
    row_index = int(row.get("row_index") or 0)
    row_id = str(row.get("row_id") or "")
    raw_url = str(row.get("url") or "")

    try:
        url = validate_url(raw_url)
    except UnsafeUrlError as error:
        # Rejected before any network access, which is the point of doing this in the domain.
        return (
            retrieval_failure(
                row_index=row_index,
                row_id=row_id,
                url=raw_url,
                reason=FailureReason.UNSAFE_URL,
                detail=str(error),
            ),
            b"",
        )

    try:
        response = transport.fetch(url, limits)
    except TransportError as error:
        return (
            retrieval_failure(
                row_index=row_index,
                row_id=row_id,
                url=raw_url,
                reason=error.reason,
                detail=error.detail,
            ),
            b"",
        )
    except Exception as error:  # a transport must never abort the whole run
        # Belt and braces behind HttpxTransport's own catch-all. One malformed row must not lose
        # every record already fetched, and the manifest is only written after this loop.
        return (
            retrieval_failure(
                row_index=row_index,
                row_id=row_id,
                url=raw_url,
                reason=FailureReason.TRANSPORT_ERROR,
                detail=f"{type(error).__name__}: {error}",
            ),
            b"",
        )

    if response.truncated:
        return (
            retrieval_failure(
                row_index=row_index,
                row_id=row_id,
                url=raw_url,
                reason=FailureReason.TOO_LARGE,
                detail=f"body exceeded the {limits.max_bytes} byte limit and the read was stopped",
            ),
            b"",
        )

    record = evaluate_response(
        row_index=row_index,
        row_id=row_id,
        url=raw_url,
        status=response.status,
        content_type=response.content_type,
        body=response.body,
        limits=limits,
        final_url=response.final_url,
    )
    return record, (response.body if record.ok else b"")


def run_retrieve(
    *,
    transport: Transport,
    rows: Sequence[Mapping[str, Any]],
    source: Mapping[str, Any],
    out_dir: pathlib.Path,
    limits: RetrievalLimits | None = None,
) -> RetrievalResult:
    """Retrieve the documents for already-selected, already-scored rows.

    ``rows`` are the rows to fetch -- the caller decides which, so this stage does not re-implement
    the relevance rule.

    Deduplication is by content, not by URL: the same PDF served from two addresses is stored once,
    and the second row records ``duplicate_of`` rather than a second copy on disk.
    """
    limits = limits or RetrievalLimits()
    seen: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    retrieved = 0

    for row in rows:
        record, body = _fetch_one(transport=transport, limits=limits, row=row)
        entry = record.as_dict()

        if record.ok and record.sha256 and record.path:
            retrieved += 1
            if record.sha256 in seen:
                entry["duplicate_of"] = str(seen[record.sha256])
            else:
                seen[record.sha256] = record.row_index
                # Written once per digest. The path is derived from the content, so re-running
                # cannot produce a second copy under a different name.
                write_bytes(out_dir / record.path, body)

        # Explicit field pick rather than a blanket merge: relying on the select manifest's
        # source block never growing a `url` or `row_index` key is a fragile contract.
        provenance = {
            **{key: source.get(key) for key in ("dataset", "revision", "config", "split", "shard")},
            "row_index": record.row_index,
            "row_id": record.row_id,
            "url": record.url,
            "sha256": record.sha256,
        }
        entry["publication"] = policy.decide(provenance, require_artifact_hash=record.ok).as_dict()
        records.append(entry)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "stage": "retrieve",
        "source": dict(source),
        "limits": limits.as_dict(),
        "counts": {
            "attempted": len(records),
            "retrieved": retrieved,
            "unique": len(seen),
            "failed": len(records) - retrieved,
        },
        "failures": _failure_counts(records),
        "records_digest": content_digest(records),
    }
    records_path = write_bytes(out_dir / RETRIEVED_NAME, canonical_jsonl(records))
    manifest_path = write_bytes(out_dir / MANIFEST_NAME, canonical_bytes(manifest) + b"\n")
    return RetrievalResult(
        manifest=manifest,
        manifest_path=manifest_path,
        records_path=records_path,
        attempted=len(records),
        retrieved=retrieved,
        unique=len(seen),
        failed=len(records) - retrieved,
    )


def _failure_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many rows failed for each reason. A run should be able to show why, not just how many."""
    counts: dict[str, int] = {}
    for record in records:
        reason = record.get("reason")
        if reason:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items()))


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
    digest = record.get("sha256")
    stored = record.get("path")
    if not isinstance(digest, str) or not isinstance(stored, str):
        raise ImageExtractionError("record has no usable sha256 and path")
    expected = artifact_path(digest)
    if stored != expected:
        raise ImageExtractionError(f"path {stored!r} is not the content-addressed {expected!r}")

    data = read_bytes(pdf_root / expected)
    actual = sha256_hex(data)
    if actual != digest:
        raise ImageExtractionError(f"stored pdf hashes to {actual}, not the recorded {digest}")
    return data


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
    row_id = str(record.get("row_id") or "")
    pdf_sha256 = str(record.get("sha256") or "")

    try:
        row_index = int(record.get("row_index") or 0)
        pdf_bytes = _pdf_bytes_for(record, pdf_root)
        extracted = extractor.extract(pdf_bytes)
        records = [
            build_image_record(
                document_row_id=row_id,
                document_row_index=row_index,
                pdf_sha256=pdf_sha256,
                page_index=image.page_index,
                image_index=image.image_index,
                data=image.data,
                mime=image.mime,
                width=image.width,
                height=image.height,
            )
            for image in extracted
        ]
    except ImageExtractionError as error:
        # A malformed document fails with a diagnostic and contributes nothing. Partial output
        # from a document we could not read would be worse than none.
        return [], {}, str(error)
    except (OSError, ValueError, TypeError) as error:
        # A missing file, a non-string path, a non-numeric row index. These used to escape
        # run_extract entirely -- and because the manifest is written after the loop, one bad row
        # lost every document already extracted. "One failure never stops the others" has to be
        # true, not just documented.
        return [], {}, f"{type(error).__name__}: {error}"
    except Exception as error:
        # A third-party ImageExtractor is not obliged to raise only ImageExtractionError.
        return [], {}, f"{type(error).__name__}: {error}"
    return records, {(i.page_index, i.image_index): i.data for i in extracted}, ""


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

        document_rows.append(
            {
                "row_index": record.get("row_index"),
                "row_id": record.get("row_id"),
                "url": record.get("url"),
                "pdf_sha256": record.get("sha256"),
                "pdf_path": record.get("path"),
                "image_count": len(images),
                "ok": not error,
                "error": error,
            }
        )
        if error:
            failed += 1
            continue
        if images:
            with_images += 1

        for image in sorted(images, key=sort_key):
            entry = image.as_dict()
            if image.sha256 in seen:
                entry["duplicate_of"] = seen[image.sha256]
            else:
                # Keyed on the PDF's digest, not its row id: a row id can be empty or repeated
                # across documents, and then the reference points at nothing resolvable.
                seen[image.sha256] = f"{image.pdf_sha256}#{image.page_index}.{image.image_index}"
                write_bytes(out_dir / image.path, payloads[(image.page_index, image.image_index)])
            image_rows.append(entry)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "stage": "extract",
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
