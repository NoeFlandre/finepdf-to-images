"""Retrieving the source PDFs, under strict bounds."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.retrieval import Transport, TransportError
from finepdf_to_images.adapters.storage import write_bytes
from finepdf_to_images.domain import allowlist, policy
from finepdf_to_images.domain.retrieval import (
    FailureReason,
    RetrievalLimits,
    RetrievalRecord,
    UnsafeUrlError,
    validate_url,
)
from finepdf_to_images.domain.retrieval import evaluate as evaluate_response
from finepdf_to_images.domain.retrieval import failure as retrieval_failure
from finepdf_to_images.domain.serialization import (
    canonical_bytes,
    canonical_jsonl,
    content_digest,
)
from finepdf_to_images.pipeline.shared import (
    MANIFEST_NAME,
    RETRIEVED_NAME,
    _identity_of,
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


def _no_bytes(
    row_index: int, row_id: str, url: str, reason: FailureReason, detail: str
) -> tuple[RetrievalRecord, bytes]:
    """A retrieval that produced no artifact, in the shape the caller expects."""
    record = retrieval_failure(
        row_index=row_index, row_id=row_id, url=url, reason=reason, detail=detail
    )
    return record, b""


def _fetch_one(
    *,
    transport: Transport,
    limits: RetrievalLimits,
    row: Mapping[str, Any],
) -> tuple[RetrievalRecord, bytes]:
    """Retrieve one row's document, converting every failure into a record rather than an error.

    Returns the record and the bytes, so the caller can store them exactly once per digest.
    """
    identity = _identity_of(row)
    row_index, row_id, raw_url = identity

    try:
        url = validate_url(raw_url)
    except UnsafeUrlError as error:
        # Rejected before any network access, which is the point of doing this in the domain.
        return _no_bytes(*identity, FailureReason.UNSAFE_URL, str(error))

    try:
        response = transport.fetch(url, limits)
    except TransportError as error:
        return _no_bytes(*identity, error.reason, error.detail)
    except Exception as error:  # a transport must never abort the whole run
        # Belt and braces behind HttpxTransport's own catch-all. One malformed row must not lose
        # every record already fetched, and the manifest is only written after this loop.
        detail = f"{type(error).__name__}: {error}"
        return _no_bytes(*identity, FailureReason.TRANSPORT_ERROR, detail)

    if response.truncated:
        detail = f"body exceeded the {limits.max_bytes} byte limit and the read was stopped"
        return _no_bytes(*identity, FailureReason.TOO_LARGE, detail)

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

        retrieved += _store(record, body, seen, entry, out_dir)

        entry["publication"] = policy.decide(
            _provenance_for(record, source),
            allowlist.declaration_for(record.url, record.final_url),
            require_artifact_hash=record.ok,
        ).as_dict()
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


def _store(
    record: RetrievalRecord,
    body: bytes,
    seen: dict[str, int],
    entry: dict[str, Any],
    out_dir: pathlib.Path,
) -> int:
    """Write a newly retrieved PDF once per digest; return 1 if it was a retrieval at all.

    Deduplication is by content, not by URL: the same document served from two addresses is one
    artifact, and the path is derived from the content, so re-running cannot produce a second copy
    under a different name.
    """
    if not (record.ok and record.sha256 and record.path):
        return 0
    if record.sha256 in seen:
        entry["duplicate_of"] = str(seen[record.sha256])
    else:
        seen[record.sha256] = record.row_index
        write_bytes(out_dir / record.path, body)
    return 1


def _provenance_for(record: RetrievalRecord, source: Mapping[str, Any]) -> dict[str, Any]:
    """Provenance for one retrieval, as an explicit field pick.

    A blanket ``{**source, **record}`` merge would rely on the select manifest's source block
    never growing a ``url`` or ``row_index`` key -- a contract nothing enforces.
    """
    return {
        **{key: source.get(key) for key in ("dataset", "revision", "config", "split", "shard")},
        "row_index": record.row_index,
        "row_id": record.row_id,
        "url": record.url,
        "sha256": record.sha256,
    }


def _failure_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many rows failed for each reason. A run should be able to show why, not just how many."""
    counts: dict[str, int] = {}
    for record in records:
        reason = record.get("reason")
        if reason:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items()))
