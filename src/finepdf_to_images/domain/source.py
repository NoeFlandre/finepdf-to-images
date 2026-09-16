"""The pinned, bounded FinePDFs input contract.

Pure. Everything here describes *what* to read and *which rows to keep*; reading it is the
adapter's job. That split is what lets the selection rules be tested without a network, a token, or
a 4.8 GB download.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finepdf_to_images.domain.serialization import content_digest, sha256_hex

#: The source dataset. Not configurable: this proof of concept is about FinePDFs specifically.
SOURCE_DATASET = "HuggingFaceFW/finepdfs"

#: An immutable commit of that dataset. A branch name would make every run unreproducible the
#: moment upstream pushes, which defeats the point of a pinned input.
DEFAULT_REVISION = "220bac3acbf07789502c621d2d33952f51ac7f86"

#: The pilot shard. Note this is the real upstream path; FinePDFs names shards
#: ``<group>_<index>.parquet``, not ``0000.parquet``.
DEFAULT_CONFIG = "eng_Latn"
DEFAULT_SPLIT = "train"
DEFAULT_SHARD = "000_00000.parquet"

#: Deliberately small. The shard holds 388,000 rows in 388 row groups of 1,000.
DEFAULT_LIMIT = 100
DEFAULT_SEED = "finepdf-to-images/v1"

#: Upstream row groups. Used only to reason about how much of the shard a limit forces us to read;
#: the adapter reports the true value it observed and the manifest records that, not this guess.
NOMINAL_ROWS_PER_ROW_GROUP = 1000

#: The columns the pipeline actually uses. Reading fewer columns is what keeps a bounded read cheap.
SELECTED_COLUMNS: tuple[str, ...] = (
    "id",
    "url",
    "date",
    "dump",
    "language",
    "full_doc_lid",
    "full_doc_lid_score",
    "token_count",
    "is_truncated",
    "extractor",
    "text",
)

_CONFIG_RE = re.compile(r"\A[a-z]{3}_[A-Z][a-z]{3}\Z")
_SPLIT_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_SHARD_RE = re.compile(r"\A[0-9]{3}_[0-9]{5}\.parquet\Z")
_REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")


class SourceConfigurationError(ValueError):
    """An input reference that cannot be trusted to name one bounded shard.

    Raised eagerly, before any network access. The pipeline must never respond to a malformed
    reference by widening its read: silently falling back to the whole dataset is the exact failure
    this proof of concept exists to avoid.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class SourceRef:
    """An immutable pointer to exactly one FinePDFs shard."""

    revision: str = DEFAULT_REVISION
    config: str = DEFAULT_CONFIG
    split: str = DEFAULT_SPLIT
    shard: str = DEFAULT_SHARD
    dataset: str = SOURCE_DATASET

    def __post_init__(self) -> None:
        _require(_REVISION_RE, self.revision, "revision", "a 40-character lowercase commit sha")
        _require(_CONFIG_RE, self.config, "config", "a FinePDFs language code such as eng_Latn")
        _require(_SPLIT_RE, self.split, "split", "a split name such as train")
        _require(_SHARD_RE, self.shard, "shard", "a shard file name such as 000_00000.parquet")

    @property
    def path(self) -> str:
        """The shard's path inside the dataset repository."""
        return f"data/{self.config}/{self.split}/{self.shard}"

    def as_dict(self) -> dict[str, str]:
        return {
            "dataset": self.dataset,
            "revision": self.revision,
            "config": self.config,
            "split": self.split,
            "shard": self.shard,
            "path": self.path,
        }


def _require(pattern: re.Pattern[str], value: str, field: str, expected: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SourceConfigurationError(
            f"invalid {field} {value!r}: expected {expected}. "
            "Refusing to continue rather than widening the read."
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SamplingSpec:
    """How many rows to keep from the shard, and which ones.

    ``head``
        Keep the first ``limit`` rows in shard order. The cheapest bounded read, and the default.
    ``hash``
        Keep the ``limit`` rows whose ``sha256(seed + row id)`` sorts lowest within the bounded
        read window. Stable across runs and machines because it derives from the row's own identity
        rather than from RNG state, so a replay needs only the seed.
    """

    limit: int = DEFAULT_LIMIT
    seed: str = DEFAULT_SEED
    strategy: str = "head"

    def __post_init__(self) -> None:
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit < 1:
            raise SourceConfigurationError(f"invalid limit {self.limit!r}: expected a positive int")
        if self.strategy not in {"head", "hash"}:
            raise SourceConfigurationError(
                f"invalid strategy {self.strategy!r}: expected 'head' or 'hash'"
            )
        if not self.seed:
            raise SourceConfigurationError("invalid seed: expected a non-empty string")

    def as_dict(self) -> dict[str, object]:
        return {"limit": self.limit, "seed": self.seed, "strategy": self.strategy}


@dataclasses.dataclass(frozen=True, slots=True)
class SourceRecord:
    """One FinePDFs row, reduced to the fields this pipeline uses.

    ``row_index`` is the row's position in the shard and is part of its provenance: together with
    the :class:`SourceRef` it locates the row exactly, even if upstream ids were ever reused.
    """

    row_index: int
    row_id: str
    url: str
    date: str
    dump: str
    language: str
    full_doc_lid: str
    full_doc_lid_score: float
    token_count: int
    is_truncated: bool
    extractor: str
    text: str

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    def without_text(self) -> dict[str, object]:
        """Provenance without the document body, for manifests that should stay readable."""
        return {key: value for key, value in self.as_dict().items() if key != "text"}


def read_window(limit: int, rows_per_row_group: int = NOMINAL_ROWS_PER_ROW_GROUP) -> int:
    """How many rows must be read to satisfy ``limit`` under ``hash`` sampling.

    Sampling by hash is only meaningful over a window larger than the sample, but the window must
    stay bounded or we are back to scanning the corpus. One row group is the natural unit: it is
    the smallest amount a Parquet reader can fetch anyway.
    """
    if rows_per_row_group < 1:
        raise SourceConfigurationError("rows_per_row_group must be positive")
    groups = -(-limit // rows_per_row_group)
    return groups * rows_per_row_group


def build_record(row_index: int, row: Mapping[str, Any]) -> SourceRecord:
    """Project one raw shard row onto :class:`SourceRecord`, rejecting unusable rows.

    A row without an id or a url cannot be traced back to FinePDFs or retrieved, so it is a
    configuration-level failure rather than something to paper over with a default.
    """
    row_id = _text(row, "id")
    url = _text(row, "url")
    if not row_id or not url:
        raise SourceConfigurationError(
            f"row {row_index} is missing an id or url and cannot be traced to its source"
        )
    return SourceRecord(
        row_index=row_index,
        row_id=row_id,
        url=url,
        date=_text(row, "date"),
        dump=_text(row, "dump"),
        language=_text(row, "language"),
        full_doc_lid=_text(row, "full_doc_lid"),
        full_doc_lid_score=float(row.get("full_doc_lid_score") or 0.0),
        token_count=int(row.get("token_count") or 0),
        is_truncated=bool(row.get("is_truncated")),
        extractor=_text(row, "extractor"),
        text=_text(row, "text"),
    )


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value)


def selection_key(record: SourceRecord, seed: str) -> str:
    """The stable pseudo-random ordering key for ``hash`` sampling."""
    return sha256_hex(f"{seed}\x00{record.row_id}".encode())


def select(records: Iterable[SourceRecord], spec: SamplingSpec) -> list[SourceRecord]:
    """Choose at most ``spec.limit`` records deterministically.

    The result is always returned in shard order regardless of strategy, so downstream artifacts
    and manifests have one stable ordering.
    """
    ordered = sorted(records, key=lambda record: record.row_index)
    if spec.strategy == "head":
        return ordered[: spec.limit]
    chosen = sorted(
        ordered, key=lambda record: (selection_key(record, spec.seed), record.row_index)
    )
    return sorted(chosen[: spec.limit], key=lambda record: record.row_index)


def build_manifest(
    *,
    ref: SourceRef,
    spec: SamplingSpec,
    records: Sequence[SourceRecord],
    rows_read: int,
    rows_per_row_group: int,
) -> dict[str, Any]:
    """The run manifest for a selection.

    Contains no timestamp and no machine detail on purpose: two runs of the same pinned input must
    produce the same bytes, and a clock reading would break that for no gain.
    """
    selected = [record.without_text() for record in records]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "stage": "select",
        "source": ref.as_dict(),
        "sampling": spec.as_dict(),
        "read": {
            "rows_read": rows_read,
            "rows_per_row_group": rows_per_row_group,
            "columns": list(SELECTED_COLUMNS),
        },
        "counts": {"selected": len(selected)},
        "records": selected,
    }
    manifest["records_digest"] = content_digest(selected)
    return manifest
