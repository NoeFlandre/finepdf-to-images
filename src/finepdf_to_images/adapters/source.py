"""Reading a bounded window of one FinePDFs shard.

The pilot shard is 4.8 GB of Parquet holding 388,000 rows in 388 row groups of 1,000. Nothing here
may download it. Both readers fetch **only the row groups a limit actually requires**, and only the
columns the pipeline uses, which for the default 100-row run is a single ~24 MB row group before
column pruning.

Everything in this module is I/O. The rules about what to keep live in
:mod:`finepdf_to_images.domain.source`.
"""

from __future__ import annotations

import pathlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from finepdf_to_images.domain.source import (
    SELECTED_COLUMNS,
    SourceConfigurationError,
    SourceRef,
)


@dataclass(frozen=True, slots=True)
class ShardWindow:
    """The bounded slice of a shard that was actually read.

    ``rows_fetched`` and ``row_groups_read`` describe the **fetch**, not the request. They are what
    a reviewer needs to audit the bounded-read claim, and they are what the tests assert on: a
    reader that pulled every row group and sliced at the end would pass a length check but not
    these.
    """

    rows: list[dict[str, Any]]
    rows_per_row_group: int
    rows_fetched: int
    row_groups_read: int
    total_rows: int
    total_row_groups: int


class ShardReader(Protocol):
    """Returns at most ``max_rows`` rows from the start of a shard.

    Only the returned ``rows`` are capped. ``rows_fetched`` may legitimately exceed ``max_rows``,
    because a row group is the smallest unit Parquet can fetch: asking for 100 rows of a shard with
    1,000-row groups fetches 1,000. That gap is exactly what the manifest exists to make visible.
    """

    def read(
        self, ref: SourceRef, *, max_rows: int, columns: Sequence[str] = SELECTED_COLUMNS
    ) -> ShardWindow: ...


def _read_bounded(open_file: Any, *, max_rows: int, columns: Sequence[str]) -> ShardWindow:
    """Shared bounded read over any seekable Parquet file object.

    Reads whole row groups because that is the smallest unit Parquet supports, stopping as soon as
    ``max_rows`` is covered.
    """
    import pyarrow.parquet as pq  # imported lazily: only a real read needs pyarrow

    if max_rows < 1:
        raise SourceConfigurationError(f"max_rows must be positive, got {max_rows}")

    parquet = pq.ParquetFile(open_file)
    metadata = parquet.metadata
    _require_columns(parquet, columns)

    rows: list[dict[str, Any]] = []
    rows_per_row_group = metadata.row_group(0).num_rows if metadata.num_row_groups else 0
    groups_read = 0
    for group in range(metadata.num_row_groups):
        table = parquet.read_row_group(group, columns=list(columns))
        rows.extend(table.to_pylist())
        groups_read += 1
        if len(rows) >= max_rows:
            break

    return ShardWindow(
        rows=rows[:max_rows],
        rows_per_row_group=rows_per_row_group,
        rows_fetched=len(rows),
        row_groups_read=groups_read,
        total_rows=metadata.num_rows,
        total_row_groups=metadata.num_row_groups,
    )


def _require_columns(parquet: Any, columns: Sequence[str]) -> None:
    """Refuse a shard whose schema is not the one we expect, rather than guessing at it."""
    available = {parquet.schema_arrow.field(i).name for i in range(len(parquet.schema_arrow))}
    missing = [column for column in columns if column not in available]
    if missing:
        raise SourceConfigurationError(
            f"shard is missing expected columns {missing}; refusing to guess at its schema"
        )


class HuggingFaceShardReader:
    """Reads the pinned shard from the Hub over range requests.

    Requires a network but no token: FinePDFs is public. The revision comes from the
    :class:`SourceRef`, so a run can never silently drift onto a newer upstream commit.
    """

    def read(
        self, ref: SourceRef, *, max_rows: int, columns: Sequence[str] = SELECTED_COLUMNS
    ) -> ShardWindow:
        from huggingface_hub import HfFileSystem

        # ref validated every component of this path at construction time, including the dataset
        # id, so nothing here can redirect the read at another repository.
        remote = f"datasets/{ref.dataset}@{ref.revision}/{ref.path}"
        try:
            handle = HfFileSystem().open(remote, "rb")
        except FileNotFoundError as error:
            raise SourceConfigurationError(
                f"shard {ref.path!r} does not exist at {ref.dataset}@{ref.revision}. "
                "Refusing to fall back to another shard or to the full dataset."
            ) from error
        with handle:
            return _read_bounded(handle, max_rows=max_rows, columns=columns)


@dataclass(frozen=True, slots=True)
class LocalShardReader:
    """Reads a shard from a local directory laid out like the dataset repository.

    This is what makes the pipeline runnable, and the acceptance tests and Docker smoke path
    deterministic, with no network and no token: point ``root`` at a directory containing
    ``data/<config>/<split>/<shard>.parquet``.
    """

    root: pathlib.Path

    def read(
        self, ref: SourceRef, *, max_rows: int, columns: Sequence[str] = SELECTED_COLUMNS
    ) -> ShardWindow:
        path = self.root / ref.path
        if not path.is_file():
            raise SourceConfigurationError(
                f"no local shard at {path}. Refusing to fall back to the Hub or to another shard."
            )
        with path.open("rb") as handle:
            return _read_bounded(handle, max_rows=max_rows, columns=columns)
