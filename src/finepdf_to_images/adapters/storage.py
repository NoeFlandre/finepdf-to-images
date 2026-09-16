"""Deterministic filesystem writes.

Artifacts are written whole or not at all: a partially written manifest that still parses is worse
than an obvious failure, because it looks like a successful run.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any


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
        _chmod_to_umask(pathlib.Path(temporary))
        pathlib.Path(temporary).replace(path)
        _fsync_directory(path.parent)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    """Read a canonical JSONL file back into plain data."""
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _chmod_to_umask(path: pathlib.Path) -> None:
    """Give ``path`` the mode a normally created file would have had.

    Best effort: a filesystem without POSIX modes (an exFAT external volume, a bind mount in a
    container) is not a reason to fail a write whose bytes are already on disk.
    """
    current = os.umask(0)
    try:
        os.umask(current)
        path.chmod(0o666 & ~current)
    except OSError:
        pass


def _fsync_directory(directory: pathlib.Path) -> None:
    """Make the rename itself durable, not just the bytes it points at.

    Best effort for the same reason: this runs *after* ``replace``, so the file is already
    published. Failing here would report an error for a write that succeeded.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
