"""Strict readers for the values a stage hands across a boundary.

A stage's output is the one input this package does not control, so it is read rather than
trusted. A missing key is a rename or a typo and is refused here; a key that is present but
null is a fact about the row and is allowed through. That distinction is the whole point of
the module -- treating missing as optional once let a renamed field publish an empty column.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain.publication.schema import PublicationError


def _index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, Mapping[str, Any]]:
    return {row.get(key): row for row in rows}


#: Fields a stage may legitimately omit even when it produced the row at all.
#:
#: Fields a stage writes as an explicit ``null`` when they do not apply to a row.
#:
#: Derived from 234 real retrieval rows rather than guessed: ``reason`` is null for a successful
#: retrieval; ``sha256`` and ``byte_size`` are null for a failed one; the policy blocks are empty
#: until it has ruled. ``final_url`` is *never* null -- it is the requested URL when no redirect
#: happened -- so it is deliberately absent from this set and a null there is a bug.
#:
#: They are *nullable*, not optional. A real stage row carries every key it ever writes — verified
#: against 234 retrieval rows, where the successful and failed rows have identical key sets — so a
#: **missing** key is always a rename or a typo, including for these. Treating them as optional
#: instead let a renamed ``byte_size`` slip through silently, which is the whole bug this guards.
_NULLABLE_FIELDS = frozenset({"reason", "sha256", "byte_size", "license", "disposition", "status"})

_MISSING = object()


def _present(row: Mapping[str, Any], key: str) -> Any:
    """The raw value at ``key``, or a refusal naming the field and what the row actually carries.

    Shared by every reader below so the diagnostic is written once: a renamed field should read
    the same whether it was a string, a number, a flag or a list.
    """
    value = row.get(key, _MISSING)
    if value is _MISSING:
        raise PublicationError(
            f"stage output is missing {key!r}. The row carries {sorted(row)}. "
            "A renamed or misspelled field would publish an empty column, so it is refused here."
        )
    return value


def _read(row: Mapping[str, Any], key: str, kind: type, default: Any) -> Any:
    """One field of a stage output, strict about the difference between absent and wrong.

    **An empty section is legitimate.** A scored document that was never retrieved has no
    retrieval block at all, and no extraction block either, so the caller passes ``{}`` and every
    field falls back to its default. That is the pipeline working.

    **A populated section missing a field is not**, even for a field that is often null. These
    readers used to coerce anything
    unexpected to ``""``, ``0`` or ``{}``, which turned a renamed or misspelled key into a
    plausible published value rather than an error -- an empty column in a public dataset, exit
    code 0. Mutation testing found exactly that: mutants replacing a lookup key survived because
    "the rows kept every documented key and the columns were simply empty".

    So a non-empty section must carry the field, with the right type, unless the field is one a
    stage genuinely writes only sometimes (:data:`_OPTIONAL_FIELDS`).
    """
    if not row:
        return default
    value = _present(row, key)
    # An explicit null is a stage saying "not applicable" for this row -- a successful retrieval
    # writes reason: null rather than dropping the key.
    if value is None and key in _NULLABLE_FIELDS:
        return default
    if isinstance(value, bool) or not isinstance(value, kind):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected {kind.__name__}."
        )
    return value


def _section(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested block. Absent when the stage did not run for this row; never silently wrong."""
    return _read(row, key, Mapping, {})


def _text(row: Mapping[str, Any], key: str) -> str:
    return _read(row, key, str, "")


def _number(row: Mapping[str, Any], key: str) -> int:
    return _read(row, key, int, 0)


def _flag(row: Mapping[str, Any], key: str) -> bool:
    """A boolean field. Separate from :func:`_number` because ``bool`` is a subclass of ``int``."""
    if not row:
        return False
    value = _present(row, key)
    if not isinstance(value, bool):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected bool."
        )
    return value


def _items(row: Mapping[str, Any], key: str) -> list[Any]:
    """A list field, required to be present and a list when the section is populated."""
    if not row:
        return []
    value = _present(row, key)
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected a list."
        )
    return list(value)
