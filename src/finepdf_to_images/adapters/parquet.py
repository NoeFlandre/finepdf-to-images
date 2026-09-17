"""Writing the published dataset as parquet.

The only module that encodes parquet. What a row *contains* is decided in
:mod:`finepdf_to_images.domain.publication` over plain values; this turns those values into the
bytes that reach the Hub.

``pyarrow`` lives here rather than in the domain for the same reason every other I/O library does:
its writer output is an encoding concern, and the architecture check refuses it in the domain.
That separation is also what makes the published bytes testable -- the row content can be asserted
without a parquet file, and the encoding can be asserted without running the pipeline.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain.publication import DATASET_FILE, PublishFile

#: Every writer option is pinned. Any of them left to pyarrow's default can move in a release and
#: silently change the published bytes, which are content-addressed: a changed byte is a changed
#: digest, a new commit, and an idempotent re-publication that is no longer a no-op.
#:
#: One field cannot be pinned through the API -- the footer's ``created_by``, which pyarrow fills
#: with its own version string. The output is therefore byte-stable for a given pyarrow version
#: and changes on upgrade, which is why the dependency is pinned exactly rather than floored.
_WRITER_OPTIONS: Mapping[str, Any] = {
    "version": "2.6",
    "compression": "zstd",
    "compression_level": 3,
    "store_schema": True,
    "write_statistics": False,
    "row_group_size": 100,
}


def _schema() -> Any:
    """The published schema.

    ``images`` is a list of ``{bytes, path}`` structs because that is what the Hub's ``Image``
    feature decodes. Declaring it any other way makes the viewer render a struct instead of a
    picture, which is the entire point of embedding the images rather than publishing them as
    loose files.
    """
    import pyarrow as pa

    image = pa.struct([pa.field("bytes", pa.binary()), pa.field("path", pa.string())])
    return pa.schema(
        [
            pa.field("pdf_url", pa.string()),
            pa.field("text", pa.string()),
            pa.field("images", pa.list_(image)),
            pa.field("matched_terms", pa.list_(pa.string())),
        ]
    )


def encode_dataset(rows: Sequence[Mapping[str, Any]]) -> PublishFile:
    """The rows as one parquet file, at the path the publication expects.

    Deterministic: the same rows encode to the same bytes in the same process and across
    processes, so re-publishing an unchanged run is an exact no-op rather than a commit that
    changes nothing.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist([dict(row) for row in rows], schema=_schema())
    buffer = io.BytesIO()
    pq.write_table(table, buffer, **_WRITER_OPTIONS)
    return PublishFile(DATASET_FILE, buffer.getvalue())
