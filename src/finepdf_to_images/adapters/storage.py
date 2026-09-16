"""Deterministic filesystem writes.

Artifacts are written whole or not at all: a partially written manifest that still parses is worse
than an obvious failure, because it looks like a successful run.
"""

from __future__ import annotations

import os
import pathlib
import tempfile


def write_bytes(path: pathlib.Path, data: bytes) -> pathlib.Path:
    """Atomically write ``data`` to ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        pathlib.Path(temporary).replace(path)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()
