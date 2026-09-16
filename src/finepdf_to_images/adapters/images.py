"""Extracting embedded images from PDF bytes.

The only module that knows about `pypdf`. It reads bytes in memory and returns plain values; what
those values *mean* is decided in :mod:`finepdf_to_images.domain.images`.

Scope, deliberately: this extracts images **embedded** in a PDF. It does not render pages, run OCR,
or infer layout. A scanned document whose every page is one big image will produce one image per
page, which is correct but is not the same thing as "the figures in this document".
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Protocol

from finepdf_to_images.domain.images import ImageExtractionError

#: Pillow format names mapped onto the media types the domain accepts.
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


class ImageExtractor(Protocol):
    """Returns every embedded image in ``pdf_bytes``, in document order."""

    def extract(self, pdf_bytes: bytes) -> list[ExtractedImage]: ...


@dataclass(frozen=True, slots=True)
class PypdfImageExtractor:
    """The real extractor.

    ``max_images`` is a bound, not a preference: a malformed or hostile PDF can declare an
    enormous number of image XObjects, and this pilot has no reason to unpack thousands from one
    document.
    """

    max_images: int = 200

    def extract(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError, PdfStreamError

        if not pdf_bytes:
            raise ImageExtractionError("cannot extract images from an empty document")

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages = list(reader.pages)
        except (PdfReadError, PdfStreamError, ValueError, OSError) as error:
            # Bounded and actionable: the caller gets the library's reason, not a traceback.
            raise ImageExtractionError(
                f"unreadable pdf: {type(error).__name__}: {error}"
            ) from error

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

    def _walk(self, pages: list[Any]) -> list[ExtractedImage]:
        self._refuse_if_too_many(pages)
        images: list[ExtractedImage] = []
        for page_index, page in enumerate(pages):
            for image_index, image in enumerate(self._page_images(page, page_index)):
                images.append(self._describe(image, page_index, image_index))
        return images

    def _refuse_if_too_many(self, pages: list[Any]) -> None:
        """Count declared images *before* decoding any of them.

        The count used to be checked while appending, which meant pypdf had already decoded a
        whole page by the time the limit was noticed. A 395 KB document declaring 300 images at
        600x600 peaked at 283 MB of resident memory before the bound fired -- so the bound did not
        bound anything. ``keys()`` reads the resource dictionary without touching the streams.
        """
        declared = 0
        for page_index, page in enumerate(pages):
            try:
                declared += len(list(page.images.keys()))
            except Exception as error:
                raise ImageExtractionError(
                    f"page {page_index} resources unreadable: {type(error).__name__}: {error}"
                ) from error
            if declared > self.max_images:
                raise ImageExtractionError(
                    f"document declares more than {self.max_images} images; refusing to unpack"
                )

    def _page_images(self, page: Any, page_index: int) -> list[Any]:
        """Every image on one page, or a diagnostic.

        The catch is deliberately broad. A real run hit ``pypdf.errors.DependencyError:
        jbig2dec binary is not available`` -- an optional external decoder this pilot has no
        reason to install -- and because that class does not inherit from anything PDF-specific it
        escaped and aborted the whole run. A document whose images need a decoder we do not have is
        a per-document failure with a reason, not a crash.
        """
        try:
            return list(page.images)
        except Exception as error:
            raise ImageExtractionError(
                f"page {page_index} images unreadable: {type(error).__name__}: {error}"
            ) from error

    def _describe(self, image: Any, page_index: int, image_index: int) -> ExtractedImage:
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
        )


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
    import PIL
    import pypdf
    from PIL import features

    return {
        "pypdf": pypdf.__version__,
        "pillow": PIL.__version__,
        "pillow_zlib": str(features.version("zlib") or "unknown"),
    }
