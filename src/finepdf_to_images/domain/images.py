"""Identity, layout and indexing for extracted images.

Pure. Decoding a PDF is the adapter's job; deciding what an extracted image *is* — its identity,
where it lives, what the manifest says about it — belongs here, where it can be tested over plain
bytes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from finepdf_to_images.domain.serialization import sha256_hex

#: Media types this pilot is willing to emit, mapped to the extension used on disk. Deliberately
#: short: an extraction that produces something else is a surprise worth failing on rather than
#: writing a file nobody can open.
SUPPORTED_MIME_TYPES: Mapping[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tif",
}

#: Magic-byte prefixes, used to confirm the extractor produced what it claimed. The same reasoning
#: as the PDF header check in retrieval: a library's label is a claim, the bytes are the evidence.
_SIGNATURES: Mapping[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


class ImageExtractionError(ValueError):
    """A PDF whose images cannot be extracted. Carries a bounded, actionable diagnostic."""


def extension_for(mime: str) -> str:
    """The on-disk extension for a supported media type."""
    try:
        return SUPPORTED_MIME_TYPES[mime]
    except KeyError as error:
        raise ImageExtractionError(
            f"unsupported image type {mime!r}; supported: {sorted(SUPPORTED_MIME_TYPES)}"
        ) from error


def matches_signature(mime: str, data: bytes) -> bool:
    """Whether ``data`` really begins with the magic bytes for ``mime``."""
    return any(data.startswith(prefix) for prefix in _SIGNATURES.get(mime, ()))


def image_path(digest: str, mime: str) -> str:
    """Content-addressed path for an image, sharded so no directory grows without bound."""
    if len(digest) != 64 or not all(character in "0123456789abcdef" for character in digest):
        raise ImageExtractionError(f"not a sha-256 hex digest: {digest!r}")
    return f"images/{digest[:2]}/{digest[2:4]}/{digest}.{extension_for(mime)}"


@dataclasses.dataclass(frozen=True, slots=True)
class ImageRecord:
    """One extracted image, linked back to the page and document it came from.

    ``page_index`` and ``image_index`` are positions, not identity: the same image bytes can appear
    on several pages, and each occurrence gets its own record pointing at one shared artifact.
    """

    document_row_id: str
    document_row_index: int
    pdf_sha256: str
    page_index: int
    image_index: int
    sha256: str
    mime: str
    width: int
    height: int
    byte_size: int
    path: str
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


#: Smallest side, in pixels, an extracted image must have to be worth publishing.
#:
#: PDFs embed their table borders and underlines as real images, and the extractor cannot tell
#: them from photographs. On the 5000-row pilot **216 of 493 published rows (44%) had a side under
#: 32px**, 178 of them a side of 1-3px -- a `282x1` table rule is an image in the technical sense
#: and worthless in every other.
#:
#: 32 is chosen on the measured distribution rather than taste. It removes the whole artifact mass
#: (every image with a side under 32px) while sitting in a flat region: raising it to 48 changes
#: the result by 7 rows out of 493. 64 would drop a further 60 images that are genuine small
#: pictures -- icons, logos, seals -- so the cut goes where the artifacts end, not deeper.
MIN_IMAGE_SIDE = 32


def is_publishable_size(width: int, height: int, min_side: int = MIN_IMAGE_SIDE) -> bool:
    """Whether an image is large enough to be worth a row.

    Both sides, not area: a `600x2` rule has a respectable area and is still a line.
    """
    return min(width, height) >= min_side


def build_image_record(
    *,
    document_row_id: str,
    document_row_index: int,
    pdf_sha256: str,
    page_index: int,
    image_index: int,
    data: bytes,
    mime: str,
    width: int,
    height: int,
) -> ImageRecord:
    """Validate one extracted image and give it its identity.

    Everything here is a refusal to guess: an empty payload, a type we do not emit, bytes that do
    not match the type they claim, or a non-positive dimension all stop the run rather than
    producing a manifest row that describes a file nobody can read.
    """
    if not data:
        raise ImageExtractionError(
            f"page {page_index} image {image_index} of {pdf_sha256} has no bytes"
        )
    if not matches_signature(mime, data):
        raise ImageExtractionError(
            f"page {page_index} image {image_index} claims {mime} but begins {data[:8]!r}"
        )
    if width < 1 or height < 1:
        raise ImageExtractionError(
            f"page {page_index} image {image_index} has a non-positive size {width}x{height}"
        )

    digest = sha256_hex(data)
    return ImageRecord(
        document_row_id=document_row_id,
        document_row_index=document_row_index,
        pdf_sha256=pdf_sha256,
        page_index=page_index,
        image_index=image_index,
        sha256=digest,
        mime=mime,
        width=width,
        height=height,
        byte_size=len(data),
        path=image_path(digest, mime),
    )


def sort_key(record: ImageRecord) -> tuple[int, int, int]:
    """Stable ordering: document, then page, then position on the page.

    Page and image order come from the PDF itself, so this is the document's own order rather than
    an arbitrary one. Two runs over the same input therefore produce the same manifest.
    """
    return (record.document_row_index, record.page_index, record.image_index)
