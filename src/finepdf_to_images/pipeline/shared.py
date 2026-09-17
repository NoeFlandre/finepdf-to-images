"""Names and helpers the stages genuinely share.

Deliberately small. Anything used by one stage lives with that stage; this module exists only
for what crosses them, so it cannot quietly become a second home for everything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MANIFEST_NAME = "manifest.json"
RECORDS_NAME = "records.jsonl"
SCORED_NAME = "scored.jsonl"
RETRIEVED_NAME = "retrieved.jsonl"
IMAGES_NAME = "images.jsonl"
DOCUMENTS_NAME = "documents.jsonl"


def _identity_of(row: Mapping[str, Any]) -> tuple[int, str, str]:
    """``(row_index, row_id, url)`` for a record, defaulted rather than assumed.

    Each ``or`` is a branch as far as complexity is concerned, so they live here instead of
    inflating the function that does the actual work.
    """
    return int(row.get("row_index") or 0), str(row.get("row_id") or ""), str(row.get("url") or "")
