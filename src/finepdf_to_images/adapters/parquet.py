"""Serialising published rows into parquet bytes.

The only module that knows about `pyarrow`. It takes plain mappings decided by
:mod:`finepdf_to_images.domain.publication` and returns bytes, so the domain never imports an
encoder and a publication plan stays a value that can be diffed, hashed and asserted on.

Bytes, not a file. A publication plan carries file *contents*; writing to a path here would mean
the domain had to know about a filesystem to learn what it is about to commit, and the idempotency
check -- compare content hashes against what the Hub already holds -- would have to read back from
disk something it had just written.

**Determinism is a requirement, not a nicety.** Publishing is idempotent: a re-run must produce
byte-identical content or it creates an empty commit on every invocation. So every writer setting
that pyarrow would otherwise choose by default -- and could change in a future release -- is pinned
below rather than inherited. The one field this module cannot control is the parquet footer's
``created_by``, which ``pyarrow`` fills with its own version string (``parquet-cpp-arrow version
25.0.1``); output is therefore byte-stable for a given pyarrow version and changes if pyarrow is
upgraded. That is why the dependency floor matters: an upgrade rewrites every published parquet.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

#: The published table's shape, exactly.
#:
#: ``images`` is a list of structs with a ``bytes`` and a ``path`` field because that -- and not a
#: bare list of binary -- is what the Hugging Face ``Image`` feature recognises, so the viewer
#: renders the images inline next to the text and ``load_dataset`` hands back PIL images with no
#: second fetch. It is the whole reason the images are embedded rather than published as loose
#: files. Do not "simplify" it: a list of ``binary`` round-trips as opaque bytes.
SCHEMA = pa.schema(
    [
        ("pdf_url", pa.string()),
        ("text", pa.string()),
        ("images", pa.list_(pa.struct([("bytes", pa.binary()), ("path", pa.string())]))),
        ("matched_terms", pa.list_(pa.string())),
    ]
)

#: Named rather than inlined so the determinism test can state what it is protecting.
#:
#: ``version``: the parquet format revision decides how values are encoded; letting pyarrow pick
#: its current default means a library upgrade silently rewrites every row.
#: ``compression``/``compression_level``: zstd at an explicit level, because "default level" is a
#: library constant, not a promise.
#: ``store_schema``: keeps the arrow schema in the footer, so ``path`` stays a nullable string
#: rather than being re-inferred as null from an all-``None`` column.
#: ``write_statistics``: on, and pinned -- min/max statistics are derived from the data, so they
#: are stable, but whether they are written at all is a default that could move.
#: ``row_group_size``: pyarrow otherwise sizes row groups from a heuristic over the table, which
#: would make the layout depend on how the rows happened to be chunked.
_WRITER_OPTIONS: dict[str, Any] = {
    "version": "2.6",
    "compression": "zstd",
    "compression_level": 3,
    "store_schema": True,
    "write_statistics": True,
    "row_group_size": 1000,
}


def rows_to_parquet(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """Serialise ``rows`` to the bytes of a single parquet file.

    Byte-identical for identical input, within a pyarrow version -- see the module docstring.

    Each row carries ``pdf_url``, ``text``, ``images`` (possibly empty) and ``matched_terms``.
    An empty ``rows`` yields a valid, readable, zero-row parquet rather than an error: a run that
    selects nothing must still publish the table, or consumers see the previous run's rows.
    """
    table = pa.Table.from_pylist(list(rows), schema=SCHEMA)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, **_WRITER_OPTIONS)
    return buffer.getvalue()
