"""Regenerate the synthetic FinePDFs shard fixture.

Run with ``uv run python tests/fixtures/build_shard_fixture.py``.

The fixture is **synthetic**, not a slice of FinePDFs. Two reasons:

1. FinePDFs is ODC-BY licensed and its rows point at third-party documents. Committing real rows
   into this repository would quietly create a redistribution question that the publication policy
   (issue #1) exists to answer carefully.
2. A synthetic shard can contain exactly the edge cases the tests need — agriculture and
   non-agriculture text, a non-HTTP url, an empty body — which a real slice cannot be relied on to.

It mirrors the real upstream schema and layout: the same column names and types, and small row
groups so the bounded-read behaviour is exercised for real.
"""

from __future__ import annotations

import pathlib

import pyarrow as pa
import pyarrow.parquet as pq

#: Small on purpose: the real shard has 1,000-row groups, and a 5-row group lets the tests prove
#: that a limit reads only the groups it needs.
ROWS_PER_ROW_GROUP = 5
ROW_COUNT = 20

FIXTURE_ROOT = pathlib.Path(__file__).parent / "shards"
CONFIG, SPLIT, SHARD = "eng_Latn", "train", "000_00000.parquet"

AGRICULTURE_TEXT = (
    "Irrigation scheduling for maize and wheat under drought stress. "
    "Soil moisture, crop yield, and fertilizer application were measured across the farm."
)
UNRELATED_TEXT = (
    "Quarterly municipal council agenda. Call to order, land acknowledgement, attendance, "
    "approval of minutes, and the treasurer's report on parking revenue."
)


def _row(index: int) -> dict[str, object]:
    agriculture = index % 3 == 0
    text = AGRICULTURE_TEXT if agriculture else UNRELATED_TEXT
    if index == 7:
        text = "   "  # empty-body edge case
    url = f"https://fixtures.invalid/doc-{index:04d}.pdf"
    if index == 11:
        url = f"ftp://fixtures.invalid/doc-{index:04d}.pdf"  # unsafe-scheme edge case
    return {
        "text": text,
        "id": f"<urn:uuid:00000000-0000-4000-8000-{index:012d}>",
        "dump": "CC-MAIN-2023-06",
        "url": url,
        "date": "2023-01-30T22:07:32+00:00",
        "file_path": f"crawl-data/CC-MAIN-2023-06/segments/fixture/{index:05d}.warc.gz",
        "offset": 1000 * index,
        "token_count": len(text.split()),
        "language": "eng_Latn",
        "page_average_lid": "eng_Latn",
        "page_average_lid_score": 0.5,
        "full_doc_lid": "eng_Latn",
        "full_doc_lid_score": 0.75,
        "per_page_languages": ["eng_Latn"],
        "is_truncated": False,
        "extractor": "docling",
        "page_ends": [len(text)],
        "fw_edu_scores": [0.5],
        "fw_edu_v2_scores": [1.0],
        "dclm_scores": [1.5],
        "ocr_quality_scores": [2.5],
        "minhash_cluster_size": 1,
        "duplicate_count": 0,
    }


SCHEMA = pa.schema(
    [
        ("text", pa.string()),
        ("id", pa.string()),
        ("dump", pa.string()),
        ("url", pa.string()),
        ("date", pa.string()),
        ("file_path", pa.string()),
        ("offset", pa.int64()),
        ("token_count", pa.int64()),
        ("language", pa.string()),
        ("page_average_lid", pa.string()),
        ("page_average_lid_score", pa.float64()),
        ("full_doc_lid", pa.string()),
        ("full_doc_lid_score", pa.float64()),
        ("per_page_languages", pa.list_(pa.string())),
        ("is_truncated", pa.bool_()),
        ("extractor", pa.string()),
        ("page_ends", pa.list_(pa.int64())),
        ("fw_edu_scores", pa.list_(pa.float64())),
        ("fw_edu_v2_scores", pa.list_(pa.float64())),
        ("dclm_scores", pa.list_(pa.float64())),
        ("ocr_quality_scores", pa.list_(pa.float64())),
        ("minhash_cluster_size", pa.int64()),
        ("duplicate_count", pa.int64()),
    ]
)


def build() -> pathlib.Path:
    path = FIXTURE_ROOT / "data" / CONFIG / SPLIT / SHARD
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([_row(index) for index in range(ROW_COUNT)], schema=SCHEMA)
    # Uncompressed and without statistics so the bytes are reproducible across pyarrow builds.
    pq.write_table(
        table,
        path,
        row_group_size=ROWS_PER_ROW_GROUP,
        compression="none",
        write_statistics=False,
        store_schema=False,
    )
    return path


if __name__ == "__main__":
    print(build())
