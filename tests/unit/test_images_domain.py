"""Image identity, layout and indexing."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.images import (
    SUPPORTED_MIME_TYPES,
    ImageExtractionError,
    ImageRecord,
    build_image_record,
    extension_for,
    image_path,
    matches_signature,
    sort_key,
)
from finepdf_to_images.domain.serialization import sha256_hex

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"
JPEG = b"\xff\xd8\xff" + b"fixture"
TIFF = b"II*\x00" + b"fixture"


def record(
    *,
    document_row_id: str = "<urn:uuid:a>",
    document_row_index: int = 0,
    page_index: int = 0,
    image_index: int = 0,
    data: bytes = PNG,
    mime: str = "image/png",
    width: int = 4,
    height: int = 4,
) -> ImageRecord:
    return build_image_record(
        document_row_id=document_row_id,
        document_row_index=document_row_index,
        pdf_sha256=sha256_hex(b"pdf"),
        page_index=page_index,
        image_index=image_index,
        data=data,
        mime=mime,
        width=width,
        height=height,
    )


# --------------------------------------------------------------------------- media types


@pytest.mark.parametrize(("mime", "extension"), sorted(SUPPORTED_MIME_TYPES.items()))
def test_every_supported_type_has_an_extension(mime: str, extension: str) -> None:
    assert extension_for(mime) == extension


@pytest.mark.parametrize("mime", ["image/gif", "image/webp", "application/pdf", "", "IMAGE/PNG"])
def test_an_unsupported_type_is_refused(mime: str) -> None:
    with pytest.raises(ImageExtractionError, match="unsupported image type"):
        extension_for(mime)


@pytest.mark.parametrize(
    ("mime", "data"), [("image/png", PNG), ("image/jpeg", JPEG), ("image/tiff", TIFF)]
)
def test_signatures_match_their_declared_type(mime: str, data: bytes) -> None:
    assert matches_signature(mime, data)


@pytest.mark.parametrize(
    ("mime", "data"),
    [
        ("image/png", JPEG),
        ("image/jpeg", PNG),
        ("image/tiff", PNG),
        ("image/png", b""),
        ("image/png", b"\x89PN"),
        ("image/gif", b"GIF89a"),
    ],
)
def test_bytes_that_do_not_match_their_type_are_rejected(mime: str, data: bytes) -> None:
    assert not matches_signature(mime, data)


# --------------------------------------------------------------------------- paths


def test_image_paths_are_content_addressed_and_sharded() -> None:
    digest = sha256_hex(PNG)
    assert image_path(digest, "image/png") == f"images/{digest[:2]}/{digest[2:4]}/{digest}.png"


def test_the_extension_follows_the_media_type() -> None:
    digest = sha256_hex(JPEG)
    assert image_path(digest, "image/jpeg").endswith(".jpg")


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, sha256_hex(b"x").upper()])
def test_a_non_digest_is_refused(digest: str) -> None:
    with pytest.raises(ImageExtractionError):
        image_path(digest, "image/png")


@given(data=st.binary(max_size=200))
def test_image_paths_never_escape_their_prefix(data: bytes) -> None:
    path = image_path(sha256_hex(data), "image/png")
    assert path.startswith("images/")
    assert ".." not in path


# --------------------------------------------------------------------------- records


def test_a_valid_image_gets_its_identity() -> None:
    built = record()
    assert built.sha256 == sha256_hex(PNG)
    assert built.byte_size == len(PNG)
    assert built.path == image_path(built.sha256, "image/png")
    assert built.duplicate_of is None


def test_the_record_links_back_to_its_page_and_document() -> None:
    built = record(page_index=3, image_index=2, document_row_index=7)
    assert (built.page_index, built.image_index, built.document_row_index) == (3, 2, 7)
    assert built.pdf_sha256


def test_an_empty_payload_is_refused() -> None:
    with pytest.raises(ImageExtractionError, match="no bytes"):
        record(data=b"")


def test_bytes_that_contradict_the_declared_type_are_refused() -> None:
    """A library's label is a claim; the bytes are the evidence."""
    with pytest.raises(ImageExtractionError, match="claims image/png"):
        record(data=JPEG)


@pytest.mark.parametrize(("width", "height"), [(0, 4), (4, 0), (-1, 4), (0, 0)])
def test_a_non_positive_dimension_is_refused(width: int, height: int) -> None:
    with pytest.raises(ImageExtractionError, match="non-positive size"):
        record(width=width, height=height)


def test_identical_bytes_always_produce_the_same_identity() -> None:
    """Deduplication depends on this: the same logo on forty pages is one artifact."""
    a = record(page_index=0, image_index=0)
    b = record(page_index=9, image_index=4)
    assert a.sha256 == b.sha256
    assert a.path == b.path


def test_records_serialize_deterministically() -> None:
    assert record().as_dict() == record().as_dict()


# --------------------------------------------------------------------------- ordering


def test_ordering_follows_document_then_page_then_position() -> None:
    records = [
        record(document_row_index=1, page_index=0, image_index=0),
        record(document_row_index=0, page_index=2, image_index=0),
        record(document_row_index=0, page_index=0, image_index=1),
        record(document_row_index=0, page_index=0, image_index=0),
    ]
    ordered = sorted(records, key=sort_key)
    assert [sort_key(r) for r in ordered] == [(0, 0, 0), (0, 0, 1), (0, 2, 0), (1, 0, 0)]


@given(
    positions=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=5),
            st.integers(min_value=0, max_value=5),
            st.integers(min_value=0, max_value=5),
        ),
        max_size=12,
    )
)
def test_sorting_is_total_and_stable(positions: list[tuple[int, int, int]]) -> None:
    records = [record(document_row_index=d, page_index=p, image_index=i) for d, p, i in positions]
    keys = [sort_key(r) for r in sorted(records, key=sort_key)]
    assert keys == sorted(keys)
