"""Scoring selected records for agriculture relevance."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.storage import write_bytes
from finepdf_to_images.domain.scoring import score as score_text
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import (
    canonical_bytes,
    canonical_jsonl,
    content_digest,
    sha256_hex,
)
from finepdf_to_images.pipeline.shared import (
    MANIFEST_NAME,
    SCORED_NAME,
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
        text = _as_text(record.get("text"), "text", position)
        result = score_text(
            text,
            language=_as_text(record.get("language"), "language", position),
        )
        relevant += int(result.relevant)
        rows.append(
            {
                "row_index": record.get("row_index"),
                "row_id": record.get("row_id"),
                "url": record.get("url"),
                "text": text,
                "text_sha256": sha256_hex(text.encode("utf-8")),
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
