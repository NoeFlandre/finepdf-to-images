"""Image extraction and indexing, against hand-written golden PDF fixtures.

The fixtures are built byte by byte in ``tests/fixtures/build_pdf_fixtures.py`` rather than by a
PDF library, so what goes *in* is fixed by this repository rather than by a dependency.

What comes *out* is asserted at the pixel level, not the encoded-byte level. PNG encoding depends
on which deflate implementation the installed Pillow wheel was built against -- this project's
macOS wheel links zlib-ng, CI's Linux wheel links plain zlib -- so the encoded bytes of an
extracted image are not portable across builds. Pinning them was tried and failed in CI, which is
how TD-007 came to be written down.
"""

from __future__ import annotations

import pathlib

import pytest

from finepdf_to_images.adapters.images import (
    ExtractedImage,
    FixtureImageExtractor,
    ImageExtractor,
    PypdfImageExtractor,
)
from finepdf_to_images.adapters.storage import read_jsonl, write_bytes
from finepdf_to_images.domain.images import ImageExtractionError
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.serialization import sha256_hex
from finepdf_to_images.pipeline import ExtractionResult, run_extract

pytestmark = pytest.mark.integration

PDF_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def pdf(name: str) -> bytes:
    return (PDF_DIR / name).read_bytes()


@pytest.fixture
def extractor() -> PypdfImageExtractor:
    return PypdfImageExtractor()


# --------------------------------------------------------------------------- extraction


def test_a_pdf_with_two_images_yields_two_deterministic_artifacts(
    extractor: PypdfImageExtractor,
) -> None:
    """The headline case from issue #4."""
    images = extractor.extract(pdf("two-images.pdf"))
    assert len(images) == 2
    assert [(i.page_index, i.image_index) for i in images] == [(0, 0), (0, 1)]
    assert [(i.width, i.height) for i in images] == [(2, 2), (3, 2)]
    assert all(i.mime == "image/png" for i in images)
    assert images[0].data != images[1].data


def pixel_digest(data: bytes) -> str:
    """SHA-256 of an image's decoded pixels, independent of how the file was encoded."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as decoded:
        return sha256_hex(decoded.convert("RGB").tobytes())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("two-images.pdf", [(2, 2, (255, 0, 0)), (3, 2, (0, 0, 255))]),
        ("duplicate-images.pdf", [(2, 2, (0, 255, 0)), (2, 2, (0, 255, 0))]),
        ("rotated-page.pdf", [(2, 2, (255, 0, 0)), (3, 2, (0, 0, 255))]),
        (
            "two-pages.pdf",
            [(2, 2, (255, 0, 0)), (3, 2, (0, 0, 255))] * 2,
        ),
    ],
)
def test_extracted_pixels_match_the_fixture_that_produced_them(
    extractor: PypdfImageExtractor,
    name: str,
    expected: list[tuple[int, int, tuple[int, int, int]]],
) -> None:
    """Golden assertion over the image *content*, which is portable.

    REGRESSION: the determinism tests only compared two runs in the same process, so a decoding
    change would have gone unnoticed. Pinning the *encoded* hashes instead was tried and failed in
    CI, because PNG bytes depend on the Pillow wheel's deflate implementation -- which is precisely
    the limitation TD-007 records.
    """
    images = extractor.extract(pdf(name))
    actual = [(i.width, i.height, i.mime) for i in images]
    assert actual == [(w, h, "image/png") for w, h, _ in expected]
    for image, (width, height, colour) in zip(images, expected, strict=True):
        assert pixel_digest(image.data) == sha256_hex(bytes(colour) * (width * height))


def test_identical_source_samples_produce_identical_artifacts(
    extractor: PypdfImageExtractor,
) -> None:
    """Deduplication rests on this: the same picture twice must hash the same."""
    images = extractor.extract(pdf("duplicate-images.pdf"))
    assert sha256_hex(images[0].data) == sha256_hex(images[1].data)


def test_extraction_is_byte_identical_across_runs(extractor: PypdfImageExtractor) -> None:
    first = extractor.extract(pdf("two-images.pdf"))
    second = extractor.extract(pdf("two-images.pdf"))
    assert [i.data for i in first] == [i.data for i in second]


def test_dimensions_come_from_the_decoded_image_not_the_declared_ones(
    extractor: PypdfImageExtractor,
) -> None:
    """The docs claim the manifest records what the artifact *is*, not what the PDF claims."""
    from io import BytesIO

    from PIL import Image

    for image in extractor.extract(pdf("two-images.pdf")):
        with Image.open(BytesIO(image.data)) as decoded:
            assert (image.width, image.height) == decoded.size


def test_a_pdf_without_images_is_a_zero_image_success(extractor: PypdfImageExtractor) -> None:
    """Most PDFs on the open web genuinely contain none. That is a result, not a failure."""
    assert extractor.extract(pdf("no-images.pdf")) == []


def test_page_rotation_does_not_change_the_extracted_bytes(
    extractor: PypdfImageExtractor,
) -> None:
    upright = extractor.extract(pdf("two-images.pdf"))
    rotated = extractor.extract(pdf("rotated-page.pdf"))
    assert [i.data for i in upright] == [i.data for i in rotated]
    assert [(i.width, i.height) for i in upright] == [(i.width, i.height) for i in rotated]


def test_pages_are_visited_in_document_order(extractor: PypdfImageExtractor) -> None:
    images = extractor.extract(pdf("two-pages.pdf"))
    assert [(i.page_index, i.image_index) for i in images] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_duplicate_images_are_extracted_as_two_occurrences(
    extractor: PypdfImageExtractor,
) -> None:
    """Extraction reports what the document contains; deduplication happens downstream."""
    images = extractor.extract(pdf("duplicate-images.pdf"))
    assert len(images) == 2
    assert images[0].data == images[1].data


@pytest.mark.parametrize("name", ["malformed.pdf", "truncated.pdf"])
def test_malformed_bytes_fail_with_a_bounded_diagnostic(
    extractor: PypdfImageExtractor, name: str
) -> None:
    with pytest.raises(ImageExtractionError) as excinfo:
        extractor.extract(pdf(name))
    assert len(str(excinfo.value)) < 500


def test_a_missing_optional_decoder_is_a_document_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, extractor: PypdfImageExtractor
) -> None:
    """REGRESSION: a real run hit `pypdf.errors.DependencyError: jbig2dec binary is not
    available`. It inherits from nothing PDF-specific, so it escaped and aborted the whole run."""
    from pypdf.errors import DependencyError

    def explode(self: object, page: object, page_index: int) -> list[object]:
        raise DependencyError("jbig2dec binary is not available.")

    monkeypatch.setattr(PypdfImageExtractor, "_page_images", explode)
    with pytest.raises(ImageExtractionError, match="jbig2dec"):
        extractor.extract(pdf("two-images.pdf"))


def test_any_unexpected_library_error_becomes_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch, extractor: PypdfImageExtractor
) -> None:
    def explode(self: object, page: object, page_index: int) -> list[object]:
        raise RuntimeError("something pypdf never documented")

    monkeypatch.setattr(PypdfImageExtractor, "_page_images", explode)
    with pytest.raises(ImageExtractionError, match="RuntimeError"):
        extractor.extract(pdf("two-images.pdf"))


def test_empty_bytes_are_refused(extractor: PypdfImageExtractor) -> None:
    with pytest.raises(ImageExtractionError, match="empty document"):
        extractor.extract(b"")


def test_a_document_declaring_too_many_images_is_refused() -> None:
    """A bound, not a preference: a hostile PDF can declare an enormous number of XObjects."""
    with pytest.raises(ImageExtractionError, match="refusing to unpack"):
        PypdfImageExtractor(max_images=1).extract(pdf("two-images.pdf"))


def test_the_limit_fires_before_a_single_image_is_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: the count was checked while appending, so pypdf had already decoded a whole
    page before the bound was noticed. A 395 KB document declaring 300 images at 600x600 peaked at
    283 MB of resident memory before refusing -- so the bound did not bound anything."""
    decoded: list[int] = []

    def spy(self: object, image: object, page_index: int, image_index: int) -> object:
        decoded.append(1)
        raise AssertionError("no image may be decoded once the limit is known to be exceeded")

    monkeypatch.setattr(PypdfImageExtractor, "_describe", spy)
    with pytest.raises(ImageExtractionError, match="refusing to unpack"):
        PypdfImageExtractor(max_images=1).extract(pdf("two-images.pdf"))
    assert decoded == []


# --------------------------------------------------------------------------- the stage


def staged(
    names: list[str], tmp_path: pathlib.Path, *, extractor: ImageExtractor | None = None
) -> tuple[ExtractionResult, pathlib.Path]:
    """Lay out a retrieve-stage output containing ``names``, then extract from it."""
    pdf_root = tmp_path / "retrieve"
    records = []
    for index, name in enumerate(names):
        data = pdf(name)
        digest = sha256_hex(data)
        path = artifact_path(digest)
        write_bytes(pdf_root / path, data)
        records.append(
            {
                "row_index": index,
                "row_id": f"<urn:uuid:{index:012d}>",
                "url": f"https://fixtures.invalid/{name}",
                "ok": True,
                "sha256": digest,
                "path": path,
            }
        )
    result = run_extract(
        extractor=extractor or PypdfImageExtractor(),
        records=records,
        pdf_root=pdf_root,
        out_dir=tmp_path / "extract",
    )
    return result, tmp_path / "extract"


def test_the_stage_indexes_every_image_with_full_provenance(tmp_path: pathlib.Path) -> None:
    result, _ = staged(["two-images.pdf"], tmp_path)
    rows = read_jsonl(result.images_path)
    assert len(rows) == 2
    for row in rows:
        assert row["document_row_id"].startswith("<urn:uuid:")
        assert len(row["pdf_sha256"]) == 64
        assert len(row["sha256"]) == 64
        assert row["mime"] == "image/png"
        assert row["width"] > 0 and row["height"] > 0
        assert row["byte_size"] > 0
        assert row["path"].startswith("images/")


def test_image_bytes_are_written_under_their_content_addressed_path(
    tmp_path: pathlib.Path,
) -> None:
    result, out = staged(["two-images.pdf"], tmp_path)
    for row in read_jsonl(result.images_path):
        stored = out / row["path"]
        assert stored.is_file()
        assert sha256_hex(stored.read_bytes()) == row["sha256"] == stored.stem
        assert stored.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_identical_images_are_stored_once_with_stable_references(
    tmp_path: pathlib.Path,
) -> None:
    result, out = staged(["duplicate-images.pdf"], tmp_path)
    rows = read_jsonl(result.images_path)
    assert len(rows) == 2
    assert rows[0]["sha256"] == rows[1]["sha256"]
    assert rows[0]["duplicate_of"] is None
    assert rows[1]["duplicate_of"] == rows[0]["pdf_sha256"] + "#0.0"
    assert len(list((out / "images").rglob("*.png"))) == 1
    assert result.unique_images == 1
    assert result.images == 2


def test_the_same_image_across_two_documents_is_one_artifact(tmp_path: pathlib.Path) -> None:
    result, out = staged(["two-images.pdf", "two-pages.pdf"], tmp_path)
    assert result.images == 6
    assert result.unique_images == 2
    assert len(list((out / "images").rglob("*.png"))) == 2


def test_a_zero_image_document_is_recorded_as_a_success(tmp_path: pathlib.Path) -> None:
    result, _ = staged(["no-images.pdf"], tmp_path)
    assert result.documents == 1
    assert result.with_images == 0
    assert result.failed == 0
    document = read_jsonl(result.documents_path)[0]
    assert document["ok"] is True
    assert document["image_count"] == 0
    assert document["error"] == ""


def test_a_malformed_document_fails_without_partial_output(tmp_path: pathlib.Path) -> None:
    result, out = staged(["malformed.pdf"], tmp_path)
    assert result.failed == 1
    assert result.images == 0
    assert not (out / "images").exists()
    document = read_jsonl(result.documents_path)[0]
    assert document["ok"] is False
    assert document["error"]


def test_one_malformed_document_does_not_stop_the_others(tmp_path: pathlib.Path) -> None:
    result, _ = staged(["malformed.pdf", "two-images.pdf"], tmp_path)
    assert result.failed == 1
    assert result.images == 2


def test_rows_are_ordered_by_document_then_page_then_position(tmp_path: pathlib.Path) -> None:
    result, _ = staged(["two-pages.pdf"], tmp_path)
    rows = read_jsonl(result.images_path)
    positions = [(r["document_row_index"], r["page_index"], r["image_index"]) for r in rows]
    assert positions == sorted(positions)
    assert positions == [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)]


def test_the_stage_is_byte_identical_across_runs(tmp_path: pathlib.Path) -> None:
    first, _ = staged(["two-images.pdf", "duplicate-images.pdf"], tmp_path / "a")
    second, _ = staged(["two-images.pdf", "duplicate-images.pdf"], tmp_path / "b")
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.images_path.read_bytes() == second.images_path.read_bytes()


def test_a_failed_retrieval_is_skipped_entirely(tmp_path: pathlib.Path) -> None:
    result = run_extract(
        extractor=PypdfImageExtractor(),
        records=[{"row_index": 0, "row_id": "a", "ok": False, "sha256": None, "path": None}],
        pdf_root=tmp_path,
        out_dir=tmp_path / "out",
    )
    assert result.documents == 0
    assert result.images == 0


def test_extracting_nothing_is_an_empty_run_not_an_error(tmp_path: pathlib.Path) -> None:
    result = run_extract(
        extractor=PypdfImageExtractor(), records=[], pdf_root=tmp_path, out_dir=tmp_path / "out"
    )
    assert result.manifest["counts"]["documents"] == 0
    assert result.images_path.read_bytes() == b""


def test_the_extractor_is_injectable(tmp_path: pathlib.Path) -> None:
    """The stage runs without pypdf at all, which is what makes the acceptance tests cheap."""
    data = pdf("no-images.pdf")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
    fixture = FixtureImageExtractor(images={data: [ExtractedImage(0, 0, png, "image/png", 4, 4)]})
    result, _ = staged(["no-images.pdf"], tmp_path, extractor=fixture)
    assert result.images == 1
    assert read_jsonl(result.images_path)[0]["width"] == 4
