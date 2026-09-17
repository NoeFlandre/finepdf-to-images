"""Serialising published rows to parquet bytes.

Two properties matter here and nothing else does. The bytes must read back as the exact schema the
Hugging Face ``Image`` feature needs -- get the ``images`` column wrong and the viewer shows opaque
binary instead of pictures -- and they must be byte-identical across runs, because publication
compares content hashes and a drifting byte makes every re-run a fresh commit.

The determinism test spends a subprocess on purpose: serialising twice inside one interpreter
shares pyarrow's loaded state and would pass even if the output depended on process-level things
like hash seeding or a per-process writer id.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
import subprocess
import sys
import textwrap

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from finepdf_to_images.adapters.parquet import rows_to_parquet

#: Spelled out here rather than imported from the adapter on purpose. Asserting the written schema
#: against the module's own constant would pass for *any* schema, including one the Hub viewer
#: cannot render -- it would only prove pyarrow round-trips whatever it was handed. This is the
#: independent statement of what the Hugging Face ``Image`` feature requires.
EXPECTED_SCHEMA = pa.schema(
    [
        ("pdf_url", pa.string()),
        ("text", pa.string()),
        ("images", pa.list_(pa.struct([("bytes", pa.binary()), ("path", pa.string())]))),
        ("matched_terms", pa.list_(pa.string())),
    ]
)

ROWS = [
    {
        "pdf_url": "https://example.org/a.pdf",
        "text": "Soil moisture and irrigation scheduling.",
        "images": [{"bytes": b"\x89PNG\r\n\x1a\n-one", "path": None}],
        "matched_terms": ["soil", "irrigation"],
    },
    {
        "pdf_url": "https://example.org/b.pdf",
        "text": "Crop rotation trials — blé d'hiver, 麦, ΔT.",
        "images": [],
        "matched_terms": [],
    },
    {
        "pdf_url": "https://example.org/c.pdf",
        "text": "Three figures.",
        "images": [
            {"bytes": b"\x89PNG-1", "path": None},
            {"bytes": b"\xff\xd8\xff-jpeg", "path": None},
            {"bytes": b"\x89PNG-3", "path": None},
        ],
        "matched_terms": ["crop rotation"],
    },
]


def read_back(data: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(data))


def test_the_written_schema_is_exactly_the_one_the_image_feature_needs() -> None:
    """A list of structs with ``bytes`` and ``path``. A bare list of binary would not render."""
    assert read_back(rows_to_parquet(ROWS)).schema.equals(EXPECTED_SCHEMA)


def test_every_row_reads_back_with_the_values_it_was_given() -> None:
    assert read_back(rows_to_parquet(ROWS)).to_pylist() == ROWS


def test_a_row_with_no_images_reads_back_as_an_empty_list_not_a_null() -> None:
    """A document whose images could not be published carries an empty list, not a broken ref."""
    assert read_back(rows_to_parquet(ROWS)).to_pylist()[1]["images"] == []


def test_a_row_with_several_images_keeps_them_in_order() -> None:
    images = read_back(rows_to_parquet(ROWS)).to_pylist()[2]["images"]
    assert [image["bytes"] for image in images] == [
        b"\x89PNG-1",
        b"\xff\xd8\xff-jpeg",
        b"\x89PNG-3",
    ]


def test_unicode_text_survives_the_round_trip_unchanged() -> None:
    """Accented Latin, CJK and Greek in one string: an encoding slip mangles rather than raises."""
    assert read_back(rows_to_parquet(ROWS)).to_pylist()[1]["text"] == ROWS[1]["text"]


def test_an_empty_row_list_still_produces_a_readable_zero_row_table() -> None:
    """A run that selects nothing must publish the empty table, not skip the file."""
    table = read_back(rows_to_parquet([]))
    assert table.num_rows == 0
    assert table.schema.equals(EXPECTED_SCHEMA)


def test_serialising_the_same_rows_twice_gives_identical_bytes() -> None:
    assert rows_to_parquet(ROWS) == rows_to_parquet(ROWS)


def test_different_rows_give_different_bytes() -> None:
    """Guards the test above from passing because the writer ignores its input."""
    assert rows_to_parquet(ROWS) != rows_to_parquet(ROWS[:1])


CHILD = textwrap.dedent(
    """
    import hashlib
    import sys

    from finepdf_to_images.adapters.parquet import rows_to_parquet
    from tests.unit.test_parquet_adapter import ROWS

    sys.stdout.write(hashlib.sha256(rows_to_parquet(ROWS)).hexdigest())
    """
)


def serialise_in_subprocess(hash_seed: str) -> str:
    root = pathlib.Path(__file__).resolve().parents[2]
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": hash_seed,
        "PYTHONPATH": f"{root / 'src'}:{root}",
    }
    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=root,
    )
    if result.returncode != 0:
        pytest.fail(f"child failed: {result.stderr}")
    return result.stdout


def test_serialising_in_a_fresh_process_gives_the_same_bytes_as_in_this_one() -> None:
    """Defeats in-process caching: two fresh interpreters, two different hash seeds, one digest.

    If the parquet footer ever picked up a per-process value -- a writer uuid, a timestamp, a
    dict order that depends on hash seeding -- this is the test that would catch it, and the
    same-process comparison above is the one that would not.
    """
    here = hashlib.sha256(rows_to_parquet(ROWS)).hexdigest()
    assert serialise_in_subprocess("1") == here
    assert serialise_in_subprocess("2") == here
