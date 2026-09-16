"""Canonical serialization and content addressing.

Every artifact this pipeline emits must be byte-identical across runs on the same inputs, so all
serialization goes through here. Pure: no filesystem, no clock, no randomness.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Sorted keys and tight separators so equal values always produce equal bytes. ``ensure_ascii`` is
#: off deliberately: FinePDF text is Unicode, and escaping it would make diffs unreadable without
#: changing identity, since we hash the UTF-8 encoding either way.
_DUMP_KWARGS: dict[str, Any] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
    "allow_nan": False,
}


def canonical_json(value: Any) -> str:
    """Serialize ``value`` to its one canonical JSON form."""
    return json.dumps(value, **_DUMP_KWARGS)


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 bytes of :func:`canonical_json`. This is what gets hashed and written."""
    return canonical_json(value).encode("utf-8")


def canonical_jsonl(values: list[Any]) -> bytes:
    """One canonical JSON document per line, trailing newline included."""
    if not values:
        return b""
    return b"".join(canonical_bytes(value) + b"\n" for value in values)


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``. The single identity function for every artifact."""
    return hashlib.sha256(data).hexdigest()


def content_digest(value: Any) -> str:
    """SHA-256 of a value's canonical serialization."""
    return sha256_hex(canonical_bytes(value))
