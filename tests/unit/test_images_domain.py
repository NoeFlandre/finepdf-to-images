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


@pytest.mark.property
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


@pytest.mark.property
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


# ------------------------------------------------------------------------------------ captions


def test_captions_are_paired_with_a_pages_images_in_order() -> None:
    """The nth image on a page takes the nth caption on that page.

    Crude, and the docstring says so: pypdf lists a page's images without their placement, so
    true nearest-caption matching would mean parsing the content stream's placement matrices.
    Order-pairing is what fits the pilot.
    """
    from finepdf_to_images.domain.images import captions_on_page

    text = "Figure 1. A wheat canopy at flowering.\nFigure 2. Leaf area index by plot."

    assert captions_on_page(text) == (
        "Figure 1. A wheat canopy at flowering.",
        "Figure 2. Leaf area index by plot.",
    )


@pytest.mark.parametrize(
    "line",
    [
        "Figure 3: canopy closure at 45 days after sowing",
        "Fig. 12 Root architecture of the drought treatment",
        "Fig 2. Plot layout",
        "Plate 4. Senescence scoring",
        "Photo 1 - the trial site",
        "Table 5. Grain yield by cultivar",
    ],
)
def test_the_usual_caption_openings_are_recognised(line: str) -> None:
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page(line) == (line,)


def test_a_page_that_names_no_figure_yields_no_caption() -> None:
    """No caption is a valid outcome. Inventing one from the nearest prose would attach a
    confident description to a picture nobody described."""
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("Annual report of the regional office.") == ()


def test_a_caption_is_bounded() -> None:
    """A caption that runs into the body text stops being a caption."""
    from finepdf_to_images.domain.images import MAX_CAPTION_CHARACTERS, captions_on_page

    long_line = "Figure 1. " + "canopy " * 100
    (caption,) = captions_on_page(long_line)

    assert len(caption) <= MAX_CAPTION_CHARACTERS


def test_a_figure_reference_inside_a_sentence_is_not_a_caption() -> None:
    """ "as shown in Figure 2" is a reference to a figure, not the figure's own description."""
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("Yields increased, as shown in Figure 2 and Table 1.") == ()


def test_empty_page_text_yields_no_captions() -> None:
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("") == ()
    assert captions_on_page("   \n  ") == ()


# ------------------------------------------------- a caption is a sentence, not a line of layout


def test_a_caption_wrapping_over_two_lines_is_joined() -> None:
    """REGRESSION: 30 of 42 published captions ended mid-sentence, at the layout's line break.

    `"...disease incidence caused by"` is worse than no caption: it reads as complete, and the
    part it was about to name is the part that mattered.
    """
    from finepdf_to_images.domain.images import captions_on_page

    text = "FIGURE 1 Photos of the obligate\nparasitic weeds Striga asiatica, red boxes.\n"

    assert captions_on_page(text) == (
        "FIGURE 1 Photos of the obligate parasitic weeds Striga asiatica, red boxes.",
    )


def test_joining_stops_at_a_sentence_end() -> None:
    from finepdf_to_images.domain.images import captions_on_page

    text = "Figure 2. Plot layout.\nThe experiment ran for three seasons and was repeated.\n"

    assert captions_on_page(text) == ("Figure 2. Plot layout.",)


def test_joining_stops_at_the_next_caption() -> None:
    from finepdf_to_images.domain.images import captions_on_page

    text = "Fig. 1 Canopy cover by plot\nFig. 2 Leaf area index by plot\n"

    assert captions_on_page(text) == (
        "Fig. 1 Canopy cover by plot",
        "Fig. 2 Leaf area index by plot",
    )


def test_a_continuation_that_is_not_prose_is_refused() -> None:
    """REGRESSION: `Fig. 1(a) Sarjoo` was followed by a bare `-`.

    A multi-column layout interleaves text from unrelated regions, so "the next line" can be a
    stray dash, a page number, or the first line of the adjacent column. Appending that makes a
    caption confidently wrong rather than merely short.
    """
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("Fig. 1(a) Sarjoo\n-\n") == ("Fig. 1(a) Sarjoo",)
    assert captions_on_page("Fig. 1(b) Swarna\n42\n") == ("Fig. 1(b) Swarna",)


def test_joining_is_bounded() -> None:
    """A caption that has not ended after a few lines is probably not a caption any more."""
    from finepdf_to_images.domain.images import MAX_CAPTION_CHARACTERS, captions_on_page

    text = "Figure 1. " + "\n".join("canopy cover measured weekly across plots" for _ in range(12))
    (caption,) = captions_on_page(text)

    assert len(caption) <= MAX_CAPTION_CHARACTERS


def test_a_sentence_reporting_a_figure_is_not_a_caption() -> None:
    """REGRESSION: `Figure 8 shows the reduction of fuel` was published as a caption.

    It is a body sentence that happens to begin a line, and the same document also carries the
    genuine `Figure 8. Reduction of fuel consumption...`.
    """
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("Figure 8 shows the reduction of fuel consumption.") == ()
    assert captions_on_page("Table 2 presents the yields by cultivar.") == ()
    assert captions_on_page("Figure 3 illustrates the plot layout.") == ()


def test_an_unpunctuated_caption_is_still_a_caption() -> None:
    """The reporting-verb rule is what discriminates, not punctuation: `FIGURE 1 Photos of...`
    is a real caption with no separator after the number."""
    from finepdf_to_images.domain.images import captions_on_page

    assert captions_on_page("FIGURE 1 Photos of the obligate parasitic weeds.") == (
        "FIGURE 1 Photos of the obligate parasitic weeds.",
    )


# --------------------------------------------------------------- a scanned page is not a figure


def _page_image(
    width: int,
    height: int,
    page: int,
    *,
    page_width: int = 612,
    page_height: int = 792,
    row_id: str = "r1",
    index: int = 0,
) -> dict[str, object]:
    return {
        "document_row_id": row_id,
        "sha256": f"{page:064d}",
        "page_index": page,
        "image_index": index,
        "width": width,
        "height": height,
        "page_width": page_width,
        "page_height": page_height,
    }


def test_a_document_of_scanned_pages_publishes_nothing() -> None:
    """A scan of a page reproduces the page's proportions, is alone on it, and is large.

    26 of 146 published images were photographs of pages -- scanned letters and typed
    correspondence. Every filter passed them: each is genuinely a large, unique image.
    """
    from finepdf_to_images.domain.images import scanned_document_pages

    images = [_page_image(846, 1153, page) for page in range(6)]

    assert scanned_document_pages(images) == frozenset(image["sha256"] for image in images)


def test_one_page_sized_image_is_not_a_scanned_document() -> None:
    """REGRESSION: a `581x722` captioned table has roughly its page's proportions too.

    A scanned document is scanned throughout; one image is not a pattern, and dropping it would
    lose a captioned table -- the kind of row this corpus is for.
    """
    from finepdf_to_images.domain.images import scanned_document_pages

    assert scanned_document_pages([_page_image(581, 722, 0)]) == frozenset()


def test_a_document_of_real_figures_is_untouched() -> None:
    from finepdf_to_images.domain.images import scanned_document_pages

    figures = [_page_image(400, 300, page) for page in range(6)]

    assert scanned_document_pages(figures) == frozenset()


def test_a_page_carrying_several_images_is_not_a_scan() -> None:
    """A scanned page is one image. Several images on a page means a laid-out page."""
    from finepdf_to_images.domain.images import scanned_document_pages

    images = [
        _page_image(846, 1153, page, index=position) for page in range(4) for position in range(2)
    ]

    assert scanned_document_pages(images) == frozenset()


def test_a_small_page_sized_image_is_not_a_scan() -> None:
    """A thumbnail with the page's proportions is not a scan of it."""
    from finepdf_to_images.domain.images import scanned_document_pages

    images = [_page_image(80, 104, page) for page in range(5)]

    assert scanned_document_pages(images) == frozenset()


def test_images_without_page_dimensions_are_never_judged_scans() -> None:
    """An index written before page dimensions were recorded cannot answer the question, and a
    filter that guesses on missing data removes rows for no reason."""
    from finepdf_to_images.domain.images import scanned_document_pages

    images = [
        {
            "sha256": f"{page:064d}",
            "page_index": page,
            "image_index": 0,
            "width": 846,
            "height": 1153,
        }
        for page in range(6)
    ]

    assert scanned_document_pages(images) == frozenset()


# ------------------------------------------------- a photograph is continuous tone, a chart is not


def test_a_chart_is_not_a_photograph() -> None:
    """Measured on all 39 published rows: line art scored 155-1,344 distinct colours, photographs
    11,136-24,995, with a gap from 1,344 to 3,038 holding nothing.

    A photograph is continuous tone -- every leaf and shadow is its own value. A chart is a
    handful of ink colours on white, however elaborate it looks.
    """
    from finepdf_to_images.domain.images import is_continuous_tone

    assert not is_continuous_tone(155)
    assert not is_continuous_tone(1344)


def test_a_photograph_is_continuous_tone() -> None:
    from finepdf_to_images.domain.images import is_continuous_tone

    assert is_continuous_tone(3038)
    assert is_continuous_tone(24995)


def test_an_image_with_no_colour_count_is_not_judged() -> None:
    """An index written before the count existed cannot answer the question, and a filter that
    guesses on missing data removes rows for no reason."""
    from finepdf_to_images.domain.images import is_continuous_tone

    assert is_continuous_tone(None)
    assert is_continuous_tone(0)


def test_the_threshold_sits_in_the_measured_gap() -> None:
    """The constant is not a taste: it sits between the two populations, not inside either."""
    from finepdf_to_images.domain.images import MIN_CONTINUOUS_TONE_COLOURS

    assert 1344 < MIN_CONTINUOUS_TONE_COLOURS < 3038
