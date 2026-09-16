"""Composition root: wires adapters into the pure domain.

Every function here is thin on purpose. If a decision looks interesting enough to argue about, it
belongs in :mod:`finepdf_to_images.domain`, where it can be tested without I/O.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.source import ShardReader
from finepdf_to_images.adapters.storage import write_bytes
from finepdf_to_images.domain.serialization import canonical_bytes, canonical_jsonl
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


@dataclass(frozen=True, slots=True)
class SelectionResult:
    manifest: dict[str, Any]
    manifest_path: pathlib.Path
    records_path: pathlib.Path
    selected: int
    rows_read: int


def run_select(
    *,
    reader: ShardReader,
    ref: SourceRef,
    spec: SamplingSpec,
    out_dir: pathlib.Path,
) -> SelectionResult:
    """Select a bounded sample from one pinned shard and write it deterministically."""
    window = reader.read(ref, max_rows=read_window(spec.limit), columns=SELECTED_COLUMNS)
    records = [build_record(index, row) for index, row in enumerate(window.rows)]
    selected = select(records, spec)

    manifest = build_manifest(
        ref=ref,
        spec=spec,
        records=selected,
        rows_read=len(window.rows),
        rows_per_row_group=window.rows_per_row_group,
    )
    manifest_path = write_bytes(out_dir / MANIFEST_NAME, canonical_bytes(manifest) + b"\n")
    records_path = write_bytes(
        out_dir / RECORDS_NAME, canonical_jsonl([record.as_dict() for record in selected])
    )
    return SelectionResult(
        manifest=manifest,
        manifest_path=manifest_path,
        records_path=records_path,
        selected=len(selected),
        rows_read=len(window.rows),
    )
