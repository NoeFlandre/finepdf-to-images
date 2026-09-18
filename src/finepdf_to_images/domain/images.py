"""Identity, layout and indexing for extracted images.

Pure. Decoding a PDF is the adapter's job; deciding what an extracted image *is* — its identity,
where it lives, what the manifest says about it — belongs here, where it can be tested over plain
bytes.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
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
    #: The figure caption on this image's page, or "" when the page names no figure.
    caption: str = ""
    #: The page's dimensions in PDF points, or 0 when they could not be read.
    page_width: float = 0.0
    page_height: float = 0.0
    #: Distinct RGB values at a pinned sample size, or 0 when not counted. Separates a
    #: photograph from a chart; see :data:`MIN_CONTINUOUS_TONE_COLOURS`.
    distinct_colours: int = 0

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


#: How much of a caption line is kept.
#:
#: A caption is one sentence about one picture. Past that, extracted PDF text has usually run
#: from the caption into the body, and what follows describes the document rather than the image.
MAX_CAPTION_CHARACTERS = 300

#: A caption *opens* a line: "Figure 3.", "Fig. 12", "Plate 4", "Photo 1", "Table 5".
#:
#: Anchored at the start deliberately. "as shown in Figure 2" is a reference to a figure, not the
#: figure's own description, and pairing that with a picture would attach a confident caption to
#: an image nobody described.
_CAPTION_OPENING = re.compile(
    r"^\s*(?:Figure|Fig\.?|Plate|Photo|Table)\s*\d+\b",
    re.IGNORECASE,
)

#: A line opening like a caption but continuing with a reporting verb is a sentence *about* a
#: figure: "Figure 8 shows the reduction of fuel". One such line was published as a caption while
#: the same document carried the real "Figure 8. Reduction of fuel consumption...".
#:
#: Checked instead of requiring punctuation after the number, because "FIGURE 1 Photos of the
#: obligate parasitic weeds" is a genuine caption with no separator at all -- requiring one would
#: cost more captions than it saves.
_REPORTING_VERB = re.compile(
    r"^\s*(?:Figure|Fig\.?|Plate|Photo|Table)\s*\d+\s+"
    r"(?:shows?|illustrates?|presents?|gives?|depicts?|displays?|summari[sz]es?|lists?|"
    r"reports?|compares?|indicates?)\b",
    re.IGNORECASE,
)

#: How many lines after the opening may be joined onto a caption.
#:
#: A caption wraps over two or three lines; something still running after that is body text the
#: layout happened to place below the figure.
MAX_CAPTION_LINES = 3

#: A continuation line must look like prose. A multi-column layout interleaves text from
#: unrelated regions, so the line below a caption can be a stray dash, a page number, or the
#: first line of the next column -- appending those makes a caption confidently wrong rather than
#: merely short.
_PROSE = re.compile(r"[A-Za-z]{3}")


def captions_on_page(text: str) -> tuple[str, ...]:
    """The figure captions written on one page, in reading order.

    Used to pair a page's images with their descriptions: the nth image on a page takes the nth
    caption. That pairing is crude and the caller says so -- pypdf lists a page's images without
    their placement, so matching an image to the caption physically nearest it would mean parsing
    the content stream's placement matrices. Order is what the pilot can rely on.

    A caption is a sentence, not a line: the PDF's line breaks fall wherever the layout put them,
    so continuation lines are joined until the sentence ends, another caption starts, the text
    stops looking like prose, or the bounds run out.

    A page with no caption line yields nothing. Falling back to the surrounding prose would
    produce a description that reads as if it were about the picture when nobody wrote it about
    anything in particular.
    """
    lines = [line.strip() for line in text.splitlines()]
    captions: list[str] = []
    for index, line in enumerate(lines):
        if line and _CAPTION_OPENING.match(line) and not _REPORTING_VERB.match(line):
            captions.append(_joined_caption(line, lines[index + 1 :]))
    return tuple(captions)


def _joined_caption(opening: str, following: Sequence[str]) -> str:
    """One caption, continued across the line breaks its layout imposed."""
    caption = opening
    for line in following[:MAX_CAPTION_LINES]:
        if _ends_a_caption(caption) or not _continues_a_caption(line):
            break
        caption = f"{caption} {line}"
    return caption[:MAX_CAPTION_CHARACTERS].rstrip()


def _ends_a_caption(caption: str) -> bool:
    return caption.rstrip().endswith((".", "?", "!")) or len(caption) >= MAX_CAPTION_CHARACTERS


def _continues_a_caption(line: str) -> bool:
    return bool(line) and not _CAPTION_OPENING.match(line) and bool(_PROSE.search(line))


#: How close an image's aspect ratio must be to its page's to count as a scan of that page.
_PAGE_ASPECT_TOLERANCE = 0.10

#: How large a page-shaped image must be before it can be a scan rather than a thumbnail.
_MIN_PAGE_SCAN_SIDE = 600

#: What share of a document's images must look like scanned pages, and how many, before the
#: document is judged scanned throughout.
#:
#: Both conditions matter. The share is what makes it a pattern rather than a coincidence; the
#: count is what protects the single full-width table on a portrait page, which has roughly its
#: page's proportions and is a perfectly good figure. Measured on a real run: the scanned
#: document was 18 of 20 page-like, ordinary documents were 0 of 4, 0 of 9, 0 of 16.
_SCANNED_DOCUMENT_SHARE = 0.5
_MIN_SCANNED_PAGES = 3


def scanned_document_pages(images: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """The digests of one document's images, when that document is a scan rather than a paper.

    A scanned PDF has one big image per page, and extracting it is correct but is not the same
    thing as extracting the figures in a document -- the extractor's own docstring says so. Those
    images pass every other filter: they are large, unique, and not repeated across pages.

    Judged per document rather than per image because a scanned document is scanned throughout.
    The single page-shaped image in an ordinary document is a full-width table or a full-page
    figure, and dropping it loses exactly the kind of row this corpus wants.

    An index that does not record page dimensions cannot answer the question, and this returns
    nothing rather than guessing: the rule needs a re-extraction to take effect.
    """
    if not _looks_scanned(len(_page_scans(images)), len(images)):
        return frozenset()
    return frozenset(str(image.get("sha256") or "") for image in images)


def _page_scans(images: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The images that look like photographs of the page they sit on."""
    alone_on_page = _pages_holding_one_image(images)
    return [
        image
        for image in images
        if image.get("page_index") in alone_on_page and _is_page_shaped(image)
    ]


def _looks_scanned(page_like: int, total: int) -> bool:
    """Whether a document's images are page scans as a pattern rather than as a coincidence."""
    return page_like >= _MIN_SCANNED_PAGES and page_like >= _SCANNED_DOCUMENT_SHARE * total


def _pages_holding_one_image(images: Sequence[Mapping[str, Any]]) -> frozenset[Any]:
    """The pages carrying exactly one image.

    A scanned page *is* one image. Several images on a page means a laid-out page with figures on
    it, whatever their shapes.
    """
    per_page: dict[Any, int] = {}
    for image in images:
        page = image.get("page_index")
        per_page[page] = per_page.get(page, 0) + 1
    return frozenset(page for page, count in per_page.items() if count == 1)


def _is_page_shaped(image: Mapping[str, Any]) -> bool:
    """Whether one image looks like a photograph of the page it sits on."""
    dimensions = _dimensions(image)
    if dimensions is None:
        return False
    page_aspect, image_aspect, long_side = dimensions
    return (
        long_side >= _MIN_PAGE_SCAN_SIDE
        and abs(image_aspect - page_aspect) / page_aspect <= _PAGE_ASPECT_TOLERANCE
    )


def _dimensions(image: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """The page aspect, the image aspect and the image's long side, or None if any is missing.

    An index written before page dimensions were recorded cannot answer the question, and a
    filter that guesses on missing data removes rows for no reason.
    """
    page_width, page_height, width, height = (
        _positive(image.get(key)) for key in ("page_width", "page_height", "width", "height")
    )
    if not (page_width and page_height and width and height):
        return None
    return page_width / page_height, width / height, max(width, height)


def _positive(value: Any) -> float:
    """A usable dimension, or 0 for anything that is not one."""
    return float(value) if isinstance(value, (int, float)) and value > 0 else 0.0


#: How many distinct colours an image must hold to be a photograph rather than line art.
#:
#: Measured on all 39 rows of the first image-caption release, counting distinct RGB values after
#: thumbnailing to :data:`COLOUR_SAMPLE_SIDE`:
#:
#: ===================================== ==================
#: line-art plots, box plots, schematics 155 - 1,344
#: photographs (grains, field sites)     11,136 - 24,995
#: ===================================== ==================
#:
#: Nothing fell between 1,344 and 3,038, so the threshold sits in a gap rather than inside either
#: population. A photograph is continuous tone -- every leaf and every shadow is its own value --
#: while a chart is a handful of ink colours on white, however elaborate it looks.
#:
#: Saturation is the metric this looks like it should be, and it fails: a coloured bar chart
#: scores higher than a genuine specimen photograph. Colour count separates the same pair by two
#: orders of magnitude.
MIN_CONTINUOUS_TONE_COLOURS = 2_000


def is_continuous_tone(distinct_colours: int | None) -> bool:
    """Whether an image is a photograph rather than a chart, from its colour count.

    ``None`` and ``0`` mean the count is unknown -- an index written before it was recorded --
    and the answer is yes, because a filter that guesses on missing data removes rows for no
    reason. The rule takes effect on re-extraction, like the page dimensions before it.
    """
    if not distinct_colours:
        return True
    return distinct_colours >= MIN_CONTINUOUS_TONE_COLOURS


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
    caption: str = "",
    page_width: float = 0.0,
    page_height: float = 0.0,
    distinct_colours: int = 0,
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
        caption=caption,
        page_width=page_width,
        page_height=page_height,
        distinct_colours=distinct_colours,
    )


def sort_key(record: ImageRecord) -> tuple[int, int, int]:
    """Stable ordering: document, then page, then position on the page.

    Page and image order come from the PDF itself, so this is the document's own order rather than
    an arbitrary one. Two runs over the same input therefore produce the same manifest.
    """
    return (record.document_row_index, record.page_index, record.image_index)
