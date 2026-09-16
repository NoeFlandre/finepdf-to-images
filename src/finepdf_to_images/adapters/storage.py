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
        # mkstemp creates 0600 and replace preserves it; published artifacts should follow the
        # process umask like any other written file.
        umask = os.umask(0)
        os.umask(umask)
        pathlib.Path(temporary).chmod(0o666 & ~umask)
        pathlib.Path(temporary).replace(path)
        _fsync_directory(path.parent)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def _fsync_directory(directory: pathlib.Path) -> None:
    """Make the rename itself durable, not just the bytes it points at."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
