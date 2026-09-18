"""Regenerate the PDF fixtures used by the extraction tests.

Run with ``uv run python tests/fixtures/build_pdf_fixtures.py``.

These PDFs are **written by hand**, byte by byte, rather than produced by a PDF library. Three
reasons:

1. They are golden fixtures. A library that changes its output between versions would change the
   expected hashes, and then the test is asserting the library's behaviour rather than ours.
2. They are tiny — a few hundred bytes each — so the repository carries no meaningful weight and a
   reviewer can read one in a text editor.
3. They can contain exactly the cases the tests need, including a page with two images, a page
   with the *same* image twice, a rotated page, and a file with no images at all. Generating those
   reliably from a rendering library is harder than writing them.

The images are raw ``FlateDecode`` RGB samples, which is the simplest embedded image form a PDF
can carry.
"""

from __future__ import annotations

import pathlib
import zlib

FIXTURE_ROOT = pathlib.Path(__file__).parent / "pdfs"


def rgb_image(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    """Uncompressed RGB samples for a solid block of ``colour``."""
    return bytes(colour) * (width * height)


def image_object(width: int, height: int, colour: tuple[int, int, int]) -> tuple[bytes, bytes]:
    """A PDF image XObject dictionary and its compressed stream."""
    # Level 9 and no zlib header variation so the bytes are reproducible across runs.
    data = zlib.compress(rgb_image(width, height, colour), 9)
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(data)} >>"
    ).encode("ascii")
    return header, data


def _assemble(objects: list[bytes]) -> bytes:
    """Serialize numbered objects into a PDF with a correct cross-reference table."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode(
        "ascii"
    )
    out += b"%%EOF\n"
    return bytes(out)


def _stream(header: bytes, data: bytes) -> bytes:
    return header + b"\nstream\n" + data + b"\nendstream"


def build_pdf(
    images: list[tuple[int, int, tuple[int, int, int]]],
    *,
    rotate: int = 0,
    pages: int = 1,
    lines: list[str] | None = None,
) -> bytes:
    """A PDF whose single resource dictionary holds ``images``, optionally on a rotated page.

    ``images`` may repeat an entry; each becomes its own XObject, which is what gives the tests a
    PDF containing the same image bytes twice.
    """
    names = [f"Im{index}" for index in range(len(images))]
    draw = b" ".join(
        f"q 50 0 0 50 {10 + 60 * i} 10 cm /{name} Do Q".encode() for i, name in enumerate(names)
    )
    for index, line in enumerate(lines or ()):
        # Helvetica is one of the 14 standard fonts, so the text needs no embedded font file --
        # which keeps the fixture readable in a text editor, the whole point of hand-writing them.
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        draw += f" BT /F1 10 Tf 10 {170 - 20 * index} Td ({escaped}) Tj ET".encode()
    content = _stream(f"<< /Length {len(draw)} >>".encode("ascii"), draw)

    page_numbers = list(range(3, 3 + pages))
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("ascii"),
    ]

    content_number = 3 + pages
    xobject_first = content_number + 1
    resources = " ".join(f"/{name} {xobject_first + index} 0 R" for index, name in enumerate(names))
    rotation = f" /Rotate {rotate}" if rotate else ""
    font = (
        " /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >>" if lines else ""
    )
    for _ in page_numbers:
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]{rotation} "
                f"/Resources << /XObject << {resources} >>{font} >> "
                f"/Contents {content_number} 0 R >>"
            ).encode("ascii")
        )
    objects.append(content)
    for width, height, colour in images:
        objects.append(_stream(*image_object(width, height, colour)))
    return _assemble(objects)


#: Fixture images are at least MIN_IMAGE_SIDE on both sides. They used to be 2x2, which the
#: extractor now discards as a page rule -- and a fixture the pipeline throws away tests nothing.
#: See ADR-0017.
FIXTURES: dict[str, bytes] = {
    # Two visually distinct images on one page: the headline case from issue #4.
    "two-images.pdf": build_pdf([(32, 32, (255, 0, 0)), (48, 32, (0, 0, 255))]),
    # The same image bytes twice, so deduplication has something to deduplicate.
    "duplicate-images.pdf": build_pdf([(32, 32, (0, 255, 0)), (32, 32, (0, 255, 0))]),
    # A valid PDF with nothing to extract. Must be a zero-image success, not a failure.
    "no-images.pdf": build_pdf([]),
    # Page rotation must not change the extracted bytes or their order.
    "rotated-page.pdf": build_pdf([(32, 32, (255, 0, 0)), (48, 32, (0, 0, 255))], rotate=90),
    # The same two images on two pages, so page ordering and indices are observable.
    "two-pages.pdf": build_pdf([(32, 32, (255, 0, 0)), (48, 32, (0, 0, 255))], pages=2),
    # Two images and two figure captions on one page: the caption pairing case (#62).
    "captioned-figures.pdf": build_pdf(
        [(32, 32, (255, 0, 0)), (48, 32, (0, 0, 255))],
        lines=["Figure 1. A wheat canopy at flowering.", "Figure 2. Leaf area index by plot."],
    ),
    # Images with page text that names no figure: a caption must not be invented.
    "uncaptioned-figures.pdf": build_pdf(
        [(32, 32, (0, 255, 0))],
        lines=["Annual report of the regional office."],
    ),
    # Not a PDF at all beyond its header: extraction must fail with a bounded diagnostic.
    "malformed.pdf": b"%PDF-1.7\nthis is not a pdf body at all\n%%EOF\n",
    # A header and nothing else.
    "truncated.pdf": b"%PDF-1.7\n",
}


def build() -> list[pathlib.Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in FIXTURES.items():
        path = FIXTURE_ROOT / name
        path.write_bytes(data)
        written.append(path)
    return written


if __name__ == "__main__":
    for path in build():
        print(f"{path}  {path.stat().st_size} bytes")
