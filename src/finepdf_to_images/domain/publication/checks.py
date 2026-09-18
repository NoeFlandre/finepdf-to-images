"""Refusals that stop a publication before it reaches the Hub.

Each check answers one question about the rows a run is about to publish, and each measures the
rows that are actually published rather than the rows the run scored -- the distinction both
caps had to learn, after one refused a valid run over text it never wrote.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain.publication.schema import (
    MAX_ARTIFACT_BYTES,
    MAX_DOCUMENT_TEXT_BYTES,
    PublicationError,
)
from finepdf_to_images.domain.serialization import content_digest


def check_text_byte_cap(
    rows: Sequence[Mapping[str, Any]], max_text_bytes: int = MAX_DOCUMENT_TEXT_BYTES
) -> None:
    """Bound the text this publication actually writes.

    The cap guards against dumping unbounded text into a public dataset, and it has to measure
    what is published to do that. It used to sum ``text`` over every **scored** row, which was
    right when the old layout published all of them; once only documents that were retrieved and
    yielded an image reach the Hub, that counted text the run never publishes -- a 5000-row sample
    was refused over 75 MB of which almost all belonged to rejects.

    Measured on the published rows, so the repetition of a document's text across its images (see
    ADR-0016) is counted honestly rather than once per document.
    """
    total = sum(len(str(row.get("text", "")).encode("utf-8")) for row in rows)
    if total > max_text_bytes:
        raise PublicationError(
            f"the {len(rows)} published row(s) carry {total} bytes of text, over the cap of "
            f"{max_text_bytes}. Text repeats across a document's images, so this counts every "
            "published row, not every distinct document."
        )


def check_artifact_byte_cap(
    rows: Sequence[Mapping[str, Any]], max_artifact_bytes: int = MAX_ARTIFACT_BYTES
) -> None:
    """Bound the third-party bytes this publication embeds.

    This is the only cap that governs *other people's work* rather than ours, which is why it is
    set an order of magnitude below anything the pilot needs: an allow-list mistake should be
    refused here rather than discovered as a multi-gigabyte redistribution on the Hub.

    It measures the ``image`` payloads of the rows actually being published, for the same reason
    the text cap does (see :func:`check_text_byte_cap`): a cap that sums what a run *extracted*
    refuses runs over bytes that never leave the machine. One row per image means the total is
    over rows, not over documents.
    """
    total = sum(len(_row_image_bytes(row)) for row in rows)
    if total > max_artifact_bytes:
        raise PublicationError(
            f"the {len(rows)} published row(s) embed {total} bytes of image data, over the cap "
            f"of {max_artifact_bytes}. This cap bounds redistributed third-party bytes, so check "
            "the allow list before raising it."
        )


def _row_image_bytes(row: Mapping[str, Any]) -> bytes:
    """The embedded bytes of one published row, or none when it carries no image.

    Every row is built around an embeddable image, so the empty cases cannot arise today. The
    check still tolerates them: a guard that raises on a shape it was meant to measure stops
    being a guard.
    """
    image = row.get("image")
    if not isinstance(image, Mapping):
        return b""
    payload = image.get("bytes")
    return payload if isinstance(payload, bytes) else b""


def check_inputs_match_extraction(
    *,
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    extract_manifest: Mapping[str, Any] | None,
) -> None:
    """Refuse stage inputs that disagree with the extract manifest that describes them.

    Pointing ``--documents`` or ``--images`` at a truncated, empty or stale file used to shrink the
    plan silently. That was survivable while a publication could only add files. Now that it also
    deletes what it does not contain, the same mistake **removes published rows and images from a
    public dataset**, with exit code 0 and no warning -- and an operator passing a stale path is an
    ordinary mistake, not an exotic one.

    The extract stage already records a content digest of exactly these two row sets, so the check
    is an equality rather than a heuristic: it names which file is wrong instead of guessing that
    "too much" is disappearing.

    A run with no extract manifest is not checked. That is the documented way to publish without
    one, and refusing it here would break callers that never had an extraction step.
    """
    if not extract_manifest:
        return
    for label, rows, key in (
        ("--documents", documents, "documents_digest"),
        ("--images", images, "images_digest"),
    ):
        expected = extract_manifest.get(key)
        if expected is None:
            continue
        actual = content_digest([dict(row) for row in rows])
        if actual != expected:
            raise PublicationError(
                f"{label} does not match the extract manifest: it describes {key} "
                f"{expected!r} but the file given hashes to {actual!r}. The file is stale, "
                "truncated, or from another run. Publishing it would delete the published rows "
                "it no longer mentions."
            )
