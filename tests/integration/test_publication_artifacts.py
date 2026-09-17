"""Artifact bytes, and the rows that carry them."""

from __future__ import annotations

import pathlib

import pytest

from finepdf_to_images.adapters.hub import FakeHub
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DATASET_FILE,
    PublicationError,
)
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.serialization import sha256_hex
from finepdf_to_images.pipeline import run_publish
from tests.integration.publication_support import (  # noqa: F401
    _card,
    _cleared_run,
    _fixture_image_root,
    _manifest_for,
    _publish_cleared,
    _published_rows,
    publish,
    run_cli,
    scratch,
    staged_inputs,
)
from tests.unit.test_publication_domain import (
    SAMPLING,
    SOURCE,
    document_row,
    image_row,
    retrieved_row,
    scored_row,
)

pytestmark = pytest.mark.integration

SELECT_MANIFEST = {"stage": "select", "source": SOURCE, "sampling": SAMPLING}

EXTRACT_MANIFEST = {"stage": "extract", "encoder": {"pypdf": "6.19.0", "pillow": "12.3.0"}}

CLEARED_PUBLICATION = {
    "disposition": "publish-artifact",
    "reason": "public-domain confirmed by curated-allowlist",
    "license": {
        "status": "declared-open",
        "identifier": "public-domain",
        "evidence": "curated-allowlist",
        "note": "ntp.niehs.nih.gov: 17 U.S.C. 105",
    },
}


def test_a_cleared_run_embeds_the_image_and_republishes_no_loose_files(
    tmp_path: pathlib.Path,
) -> None:
    """Cleared image bytes now ride inside the row; the PDF is no longer republished at all.

    The loose `images/ab/cd/<digest>.png` files were invisible in the viewer and reachable only by
    joining two JSONL files by hand, which is what this layout replaces.
    """
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    hub = FakeHub()
    result = _publish_cleared(hub, run, apply=True)

    assert set(hub.files) == {CARD_FILE, DATASET_FILE}
    assert not result.missing
    assert _published_rows(hub)[0]["image"]["bytes"] == b"\x89PNG\r\n\x1a\npixels"


def test_the_published_row_points_at_the_bytes_that_shipped(tmp_path: pathlib.Path) -> None:
    """The index and the payload are one statement, so the row's path must name a real file."""
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    result = _publish_cleared(FakeHub(), run)

    import io

    import pyarrow.parquet as pq

    dataset = next(file for file in result.plan.files if file.path == DATASET_FILE)
    rows = pq.read_table(io.BytesIO(dataset.data)).to_pylist()
    assert rows, "a cleared image must be embedded in a row, not merely referenced"
    assert rows[0]["image"]["bytes"] == b"\x89PNG\r\n\x1a\npixels"


def test_bytes_that_disagree_with_the_index_are_refused(tmp_path: pathlib.Path) -> None:
    """A truncated write or an edited working copy must not publish under the indexed digest."""
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    (run["pdf_root"] / artifact_path(sha256_hex(b"%PDF-1.4 cleared"))).write_bytes(b"%PDF- other")

    with pytest.raises(PublicationError, match="refusing to publish bytes under a digest"):
        _publish_cleared(FakeHub(), run)


def test_omitting_a_root_is_refused_rather_than_silently_publishing_less(
    tmp_path: pathlib.Path,
) -> None:
    """REGRESSION: a forgotten root used to shrink the plan, which now *deletes* published bytes.

    Dropping artifacts from the plan was survivable while publication could only add files -- the
    bytes just were not uploaded that run. Once a publication also removes what it does not
    contain, the same forgotten flag deletes already-published bytes from a public dataset, with
    exit code 0 and no warning.
    """
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    with pytest.raises(PublicationError, match="--pdf-root was not given"):
        run_publish(
            hub=FakeHub(),
            repo="NoeFlandre/finepdf-to-images-poc",
            select_manifest=SELECT_MANIFEST,
            scored=run["scored"],
            retrieved=run["retrieved"],
            documents=run["documents"],
            images=run["images"],
            extract_manifest=EXTRACT_MANIFEST,
        )


def test_a_missing_artifact_file_fails_loudly(tmp_path: pathlib.Path) -> None:
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    (run["pdf_root"] / artifact_path(sha256_hex(b"%PDF-1.4 cleared"))).unlink()
    with pytest.raises(OSError):
        _publish_cleared(FakeHub(), run)


def test_publishing_cleared_bytes_twice_is_still_a_no_op(tmp_path: pathlib.Path) -> None:
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    hub = FakeHub()
    _publish_cleared(hub, run, apply=True)
    commits = len(hub.commits)
    second = _publish_cleared(hub, run, apply=True)
    assert second.noop
    assert len(hub.commits) == commits, "artifacts must not re-upload on every run"


def test_a_document_with_no_images_is_not_published() -> None:
    """This is finepdf-to-images: a row carrying no picture does not show what the pilot is for.

    The scorer marks both documents relevant, but only one has an extracted image, so only one is
    published. Before this filter the other shipped with an empty `images` column.
    """
    hub = FakeHub()
    result = run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(0, relevant=True), scored_row(1, relevant=True)],
        retrieved=[retrieved_row(0), retrieved_row(1)],
        documents=[document_row(0), document_row(1)],
        images=[image_row(0)],
        extract_manifest=EXTRACT_MANIFEST,
        image_root=_fixture_image_root(),
        apply=True,
    )
    assert result.plan.manifest["counts"]["relevant"] == 2
    rows = _published_rows(hub)
    assert len(rows) == 1, "the document without an image contributes no rows"
    assert rows[0]["image"], "every published row carries its picture"


def test_every_published_row_carries_an_image() -> None:
    """The invariant the shape buys: a reader never meets a row that shows nothing."""
    hub = FakeHub()
    publish(hub, apply=True, documents=5)
    rows = _published_rows(hub)
    assert rows
    assert all(row["image"] and row["image"]["bytes"] for row in rows)


def test_an_image_from_an_uncleared_source_is_still_published() -> None:
    """The licence filter on images is gone by the dataset owner's decision.

    `retrieved_row(0)` carries no allow-list clearance, so under the previous policy its image
    was indexed but never shipped. It now ships, and the card carries the takedown route in place
    of the filter.
    """
    hub = FakeHub()
    publish(hub, apply=True)
    assert _published_rows(hub)[0]["image"]["bytes"] == b"img-0-0-0"


def test_the_card_states_the_images_are_not_licence_cleared_and_gives_a_takedown_route() -> None:
    """Publishing uncleared images is only defensible if the card says so plainly."""
    hub = FakeHub()
    publish(hub, apply=True)
    card = hub.files[CARD_FILE].decode()
    assert "most carry no declared licence" in card
    assert "Takedown" in card
    assert "github.com/NoeFlandre/finepdf-to-images/issues" in card


def test_a_run_that_extracted_nothing_publishes_an_empty_table_rather_than_failing() -> None:
    hub = FakeHub()
    run_publish(
        hub=hub,
        repo="a/b",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(0, relevant=True)],
        retrieved=[retrieved_row(0)],
        documents=[document_row(0)],
        images=[],
        extract_manifest=EXTRACT_MANIFEST,
        apply=True,
    )
    assert _published_rows(hub) == []


def test_the_card_reports_the_published_row_count_not_the_relevant_count() -> None:
    """REGRESSION: the card said "52 of 1000" while the published table held 10 rows.

    The two diverged the moment documents without an image stopped being published, and the card
    took its number from the scorer rather than from the table it describes.
    """
    hub = FakeHub()
    result = run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        # Two relevant documents, one image: the scorer says 2, the table holds 1.
        scored=[scored_row(0, relevant=True), scored_row(1, relevant=True)],
        retrieved=[retrieved_row(0), retrieved_row(1)],
        documents=[document_row(0), document_row(1)],
        images=[image_row(0)],
        extract_manifest=EXTRACT_MANIFEST,
        image_root=_fixture_image_root(),
        apply=True,
    )
    card = hub.files[CARD_FILE].decode()
    published = len(_published_rows(hub))

    assert result.plan.manifest["counts"]["relevant"] == 2
    assert published == 1, "fixture must exercise the gap between relevant and published"
    assert "One row per image: 1 image" in card
    assert "52 of 1000" not in card


def test_the_card_does_not_claim_a_host_is_cleared_when_listing_no_images() -> None:
    """The old sentence rendered as ``Only ``a`, `b`` is ...`` -- broken markup, wrong grammar,
    and it named allow-listed hosts that contributed no image to the table."""
    hub = FakeHub()
    publish(hub, apply=True)
    licensing = hub.files[CARD_FILE].decode().split("## Licensing")[1].split("## Use")[0]
    assert "``" not in licensing, "no doubled backticks in the licensing paragraph"
    assert "is separately confirmed as" not in licensing
    assert "copyright remains with its original owner" in licensing


def test_the_card_uses_a_singular_noun_for_a_single_row() -> None:
    """It read "1 documents". A generated card is read by strangers; it should be literate."""
    hub = FakeHub()
    publish(hub, apply=True)
    card = hub.files[CARD_FILE].decode()
    assert "1 image extracted" in card
    assert "1 images" not in card


def test_the_image_column_is_scalar_so_the_viewer_renders_it() -> None:
    """REGRESSION: a list-of-images column displayed as raw JSON in the viewer.

    `datasets-server` typed it correctly as List(Image) and served every asset, so the data was
    never wrong -- but the viewer renders a list column as JSON and only renders a *scalar* Image
    as a picture. The headline feature of a dataset called finepdf-to-images was invisible to
    anyone who had not written code against it.

    Asserted on the parquet schema rather than the values, because the schema is what the Hub
    reads to decide how to display the column.
    """
    import io

    import pyarrow.parquet as pq

    hub = FakeHub()
    publish(hub, apply=True)
    schema = pq.read_schema(io.BytesIO(hub.files[DATASET_FILE]))

    import pyarrow as pa

    field_type = schema.field("image").type
    assert pa.types.is_struct(field_type), "image is a scalar {bytes, path} struct, never a list"
    assert {sub.name for sub in field_type} == {"bytes", "path"}
    assert "images" not in schema.names, "the list column must be gone, not merely supplemented"


def test_a_document_contributes_one_row_per_image() -> None:
    """The shape change: rows are images now, and a document's context repeats across them."""
    hub = FakeHub()
    images = [image_row(0, page=page) for page in range(3)]
    root = scratch("multi-image")
    for index, image in enumerate(images):
        data = f"page-{index}".encode()
        image["sha256"] = sha256_hex(data)
        target = root / image_path(image["sha256"], "image/png")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    run_publish(
        hub=hub,
        repo="a/b",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(0, relevant=True)],
        retrieved=[retrieved_row(0)],
        documents=[document_row(0)],
        images=images,
        extract_manifest=EXTRACT_MANIFEST,
        image_root=root,
        apply=True,
    )
    rows = _published_rows(hub)
    assert len(rows) == 3, "one document, three images, three rows"
    assert len({row["pdf_url"] for row in rows}) == 1, "the document's url repeats"
    assert len({row["text"] for row in rows}) == 1, "its text repeats, so each row stands alone"
