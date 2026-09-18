"""Extracting embedded images from PDF bytes.

The only module that knows about `pypdf`. It reads bytes in memory and returns plain values; what
those values *mean* is decided in :mod:`finepdf_to_images.domain.images`.

Scope, deliberately: this extracts images **embedded** in a PDF. It does not render pages, run OCR,
or infer layout. A scanned document whose every page is one big image will produce one image per
page, which is correct but is not the same thing as "the figures in this document".
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from finepdf_to_images.domain.images import ImageExtractionError, captions_on_page

#: Pillow format names mapped onto the media types the domain accepts.
logger = logging.getLogger(__name__)

#: The bound each image is thumbnailed to before its colours are counted.
#:
#: Pinned so the count is a property of the picture rather than of its resolution: the same figure
#: scanned at 300dpi and at 600dpi must land on the same side of the threshold. 200px is large
#: enough to keep a photograph's tonal range and small enough that counting is trivial.
COLOUR_SAMPLE_SIDE = 200

_PIL_FORMAT_TO_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "TIFF": "image/tiff"}


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    """One image found inside a PDF, in the order the document presents it."""

    page_index: int
    image_index: int
    data: bytes
    mime: str
    width: int
    height: int
    #: The figure caption written on this image's page, or "" when the page names no figure.
    caption: str = ""
    #: The page's own dimensions, in PDF points. Kept so the domain can tell a figure from a
    #: photograph *of* the page: a scan reproduces the page's proportions.
    page_width: float = 0.0
    page_height: float = 0.0
    #: Distinct RGB values at a pinned sample size, so the domain can tell a photograph from a
    #: chart without decoding anything itself.
    distinct_colours: int = 0


class ImageExtractor(Protocol):
    """Returns every embedded image in ``pdf_bytes``, in document order."""

    def extract(self, pdf_bytes: bytes) -> list[ExtractedImage]: ...


@dataclass(frozen=True, slots=True)
class PypdfImageExtractor:
    """The real extractor.

    ``max_images`` and ``max_pages`` are bounds, not preferences: a malformed or hostile PDF can
    declare an enormous number of images, and this pilot has no reason to unpack thousands from
    one document.

    ``max_images`` **truncates**; it used to refuse the whole document. Refusing meant a gazette
    declaring 201 images yielded nothing at all, which threw away 200 usable images to avoid
    unpacking one too many -- and it counted every page's declared images up front, which for
    inline images is itself the expensive part. Stopping at the cap bounds the decoding just as
    well and leaves the output usable -- but only because the names are listed without decoding
    first. Iterating ``page.images`` instead decodes the whole page before the cap is consulted,
    which a reviewer measured at 57 MB peak on a 57 KB document to return one image. The cost of
    truncating is that a truncated document's ``image_count`` is a floor rather than a total,
    which the published row records.

    Neither bound is complete. pypdf decodes a page's **inline** images (the ``BI``/``ID``/``EI``
    operators) while merely listing that page's image names, so the work happens before any count
    can be consulted. A small document carrying 300 flate-compressed 600x600 inline images peaks
    at several hundred MB before the limit fires -- 307 MB and 383 MB in two measurements.
    ``max_pages`` bounds how many pages can do that; bounding a single page would mean replacing
    pypdf's content-stream parser. See TD-008.
    """

    max_images: int = 200
    #: Pages are the unit of work that can be bounded cheaply, since each one is parsed whether or
    #: not it turns out to contain images.
    max_pages: int = 300

    def extract(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        from pypdf import PdfReader

        if not pdf_bytes:
            raise ImageExtractionError("cannot extract images from an empty document")

        pages = self._pages(PdfReader, pdf_bytes)
        try:
            return self._walk(pages)
        except ImageExtractionError:
            raise
        except Exception as error:
            # Defence in depth behind the per-page and per-image catches. Nothing pypdf or Pillow
            # can raise may abort a whole run: one unreadable document is one recorded failure.
            raise ImageExtractionError(
                f"extraction failed: {type(error).__name__}: {error}"
            ) from error

    def _pages(self, reader_class: Any, pdf_bytes: bytes) -> list[Any]:
        """Open the document and bound how many pages may be walked."""
        try:
            reader = reader_class(io.BytesIO(pdf_bytes))
            if len(reader.pages) > self.max_pages:
                raise ImageExtractionError(
                    f"document has more than {self.max_pages} pages; refusing to walk it"
                )
            return list(reader.pages)
        except ImageExtractionError:
            raise
        except Exception as error:
            # Deliberately broad, bounded and actionable: the caller gets the library's reason,
            # not a traceback. pypdf raises DependencyError for missing external decoders, which
            # inherits from nothing PDF-specific.
            raise ImageExtractionError(
                f"unreadable pdf: {type(error).__name__}: {error}"
            ) from error

    def _walk(self, pages: list[Any]) -> list[ExtractedImage]:
        images: list[ExtractedImage] = []
        for page_index, page in enumerate(pages):
            captions = self._captions(page, page_index)
            page_size = self._page_size(page)
            for image_index, name in enumerate(self._image_names(page, page_index)):
                if len(images) >= self.max_images:
                    return images
                image = self._image_at(page, name, page_index)
                caption = captions[image_index] if image_index < len(captions) else ""
                images.append(self._describe(image, page_index, image_index, caption, page_size))
        return images

    def _page_size(self, page: Any) -> tuple[float, float]:
        """The page's dimensions in points, or zeroes when the box cannot be read.

        Zeroes are a usable answer: the domain treats a missing page size as "cannot tell" and
        never judges such an image a scan, rather than guessing from the image alone.
        """
        try:
            box = page.mediabox
            return float(box.width), float(box.height)
        except Exception:  # a malformed box is one page's geometry, never the document
            return 0.0, 0.0

    def _captions(self, page: Any, page_index: int) -> tuple[str, ...]:
        """The figure captions on one page, or none if its text cannot be read.

        Text extraction is a separate failure surface from image extraction: a page whose fonts
        pypdf cannot decode still has perfectly good images in it, and losing them to a missing
        caption would be the wrong trade. Same rule the extractor already applies per image.
        """
        try:
            return captions_on_page(page.extract_text() or "")
        except Exception as error:  # one page's text, never the whole document
            logger.debug("page %d text unreadable: %s", page_index, error)
            return ()

    def _image_names(self, page: Any, page_index: int) -> list[Any]:
        """The *names* of a page's images, without decoding any of them.

        ``page.images`` is a lazy sequence whose every access decodes an image -- bytes and a PIL
        object. Iterating it therefore decodes the whole page before any cap can be consulted,
        which is what made a 57 KB document peak at 57 MB. Listing ``keys()`` first, and fetching
        only the images actually wanted, keeps the decoding bounded by ``max_images`` rather than
        by what the document declares.

        The catch is deliberately broad, for the same reason as :meth:`_image_at`.
        """
        try:
            return list(page.images.keys())
        except Exception as error:
            raise ImageExtractionError(
                f"page {page_index} images unreadable: {type(error).__name__}: {error}"
            ) from error

    def _image_at(self, page: Any, name: Any, page_index: int) -> Any:
        """Decode exactly one image.

        The catch is deliberately broad. A real run hit ``pypdf.errors.DependencyError:
        jbig2dec binary is not available`` -- an optional external decoder this pilot has no
        reason to install -- and because that class does not inherit from anything PDF-specific it
        escaped and aborted the whole run. A document whose images need a decoder we do not have is
        a per-document failure with a reason, not a crash.
        """
        try:
            return page.images[name]
        except Exception as error:
            raise ImageExtractionError(
                f"page {page_index} images unreadable: {type(error).__name__}: {error}"
            ) from error

    def _describe(
        self,
        image: Any,
        page_index: int,
        image_index: int,
        caption: str = "",
        page_size: tuple[float, float] = (0.0, 0.0),
    ) -> ExtractedImage:
        """Read one image's bytes and its true dimensions.

        Dimensions come from the decoded image rather than from the PDF's ``/Width`` and
        ``/Height`` entries: those are what the document *claims*, and a manifest should record
        what the artifact actually is.
        """
        try:
            decoded = image.image
            data = bytes(image.data)
            width, height = decoded.size
            fmt = decoded.format or ""
        except Exception as error:
            raise ImageExtractionError(
                f"page {page_index} image {image_index} could not be decoded: "
                f"{type(error).__name__}: {error}"
            ) from error

        colours = _distinct_colours(decoded)
        mime = _PIL_FORMAT_TO_MIME.get(fmt.upper())
        if mime is None:
            raise ImageExtractionError(
                f"page {page_index} image {image_index} has unsupported format {fmt!r}"
            )
        return ExtractedImage(
            page_index=page_index,
            image_index=image_index,
            data=data,
            mime=mime,
            width=int(width),
            height=int(height),
            caption=caption,
            page_width=page_size[0],
            page_height=page_size[1],
            distinct_colours=colours,
        )


def _distinct_colours(decoded: Any) -> int:
    """How many distinct RGB values an image holds, at a pinned sample size.

    A photograph is continuous tone and a chart is a few inks on white, which is what separates
    them; see ``MIN_CONTINUOUS_TONE_COLOURS`` in the domain.

    Counted here rather than in the domain because it needs decoded pixels, and Pillow is banned
    from the domain by the architecture check. A failure to count returns 0, which the domain
    reads as "unknown" and lets through.
    """
    try:
        sample = decoded.convert("RGB")
        sample.thumbnail((COLOUR_SAMPLE_SIDE, COLOUR_SAMPLE_SIDE))
        # getcolors rather than set(getdata()): exact, faster, and not deprecated. The cap is
        # above any 200px sample's possible palette, so it never returns None here.
        return len(sample.getcolors(maxcolors=COLOUR_SAMPLE_SIDE * COLOUR_SAMPLE_SIDE) or ())
    except Exception:  # one image's palette, never the document
        return 0


@dataclass(frozen=True, slots=True)
class FixtureImageExtractor:
    """Returns canned images keyed by the PDF's bytes. Lets the pipeline be tested without pypdf."""

    images: dict[bytes, list[ExtractedImage]]

    def extract(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        if pdf_bytes not in self.images:
            raise ImageExtractionError("no fixture registered for these bytes")
        return list(self.images[pdf_bytes])


def encoder_versions() -> dict[str, str]:
    """The libraries whose decoding determines the published image bytes.

    Recorded in every extract manifest because the encoded bytes are not portable across Pillow
    builds: a wheel linked against zlib-ng produces different PNG bytes from one linked against
    plain zlib, for identical pixels. A run should say what produced it. See TD-007.
    """
    versions = {"pypdf": "unavailable", "pillow": "unavailable", "pillow_zlib": "unavailable"}
    try:
        import PIL
        import pypdf
        from PIL import features
    except ImportError:
        # The stage can run on an injected extractor with neither library installed; a manifest
        # should say so rather than refuse to be written.
        return versions
    versions["pypdf"] = pypdf.__version__
    versions["pillow"] = PIL.__version__
    versions["pillow_zlib"] = str(features.version("zlib") or "unknown")
    return versions
