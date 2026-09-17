"""The minimal published dataset: four columns, relevant rows only, and a card a reader can read."""

from __future__ import annotations

from typing import Any

import yaml

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.dataset import (
    CONFIG_NAME,
    DATASET_FIELDS,
    DATASET_FILE,
    SPLIT_NAME,
    build_dataset_rows,
    render_card,
)

REPO = "NoeFlandre/finepdf-to-images-poc"
SOURCE = {
    "dataset": "HuggingFaceFW/finepdfs",
    "revision": "220bac3acbf07789502c621d2d33952f51ac7f86",
    "config": "eng_Latn",
    "split": "train",
    "shard": "000_00000.parquet",
}
SAMPLING = {"limit": 1000, "seed": "finepdf-to-images/v1", "strategy": "head"}


def document(
    row_id: str,
    *,
    relevant: bool = True,
    url: str | None = None,
    text: str = "sample text",
    matched_terms: tuple[str, ...] = ("soil", "irrigation"),
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "url": url or f"https://fixtures.invalid/{row_id}.pdf",
        "text": text,
        "relevant": relevant,
        "matched_terms": list(matched_terms),
        "image_count": 0,
        "disposition": "publish-artifact" if relevant else "metadata-only",
    }


def image(row_id: str, digest: str, *, page_index: int = 0, image_index: int = 0) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "sha256": digest,
        "mime": "image/png",
        "page_index": page_index,
        "image_index": image_index,
        "image": f"images/{digest[:2]}/{digest[2:4]}/{digest}.png",
    }


def digest(marker: str) -> str:
    return marker * 64


def card(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {"repo": REPO, "source": SOURCE, "sampling": SAMPLING}
    kwargs.update(overrides)
    return render_card(**kwargs)


def front_matter(text: str) -> dict[str, Any]:
    _, block, _ = text.split("---\n", 2)
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict)
    return parsed


def test_a_relevant_document_becomes_one_row_with_exactly_the_four_columns() -> None:
    rows = build_dataset_rows(documents=[document("a")], images=[], image_bytes={})

    assert len(rows) == 1
    assert set(rows[0]) == {"pdf_url", "text", "images", "matched_terms"}
    assert rows[0]["pdf_url"] == "https://fixtures.invalid/a.pdf"
    assert rows[0]["text"] == "sample text"
    assert rows[0]["matched_terms"] == ["soil", "irrigation"]


def test_documents_the_scorer_rejected_are_left_out_entirely() -> None:
    rows = build_dataset_rows(
        documents=[document("a", relevant=False), document("b"), document("c", relevant=False)],
        images=[],
        image_bytes={},
    )

    assert [row["pdf_url"] for row in rows] == ["https://fixtures.invalid/b.pdf"]


def test_an_irrelevant_documents_images_are_not_published_with_someone_elses_row() -> None:
    rows = build_dataset_rows(
        documents=[document("a", relevant=False), document("b")],
        images=[image("a", digest("a"))],
        image_bytes={digest("a"): b"png-a"},
    )

    assert [row["images"] for row in rows] == [[]]


def test_rows_keep_the_shard_order_they_arrived_in() -> None:
    order = ["c", "a", "b"]
    rows = build_dataset_rows(
        documents=[document(row_id) for row_id in order], images=[], image_bytes={}
    )

    assert [row["pdf_url"] for row in rows] == [
        f"https://fixtures.invalid/{row_id}.pdf" for row_id in order
    ]


def test_a_rows_images_are_ordered_by_page_then_index_however_they_arrive() -> None:
    images = [
        image("a", digest("d"), page_index=2, image_index=0),
        image("a", digest("b"), page_index=0, image_index=1),
        image("a", digest("c"), page_index=1, image_index=0),
        image("a", digest("a"), page_index=0, image_index=0),
    ]
    rows = build_dataset_rows(
        documents=[document("a")],
        images=images,
        image_bytes={digest(marker): marker.encode() for marker in "abcd"},
    )

    assert [entry["bytes"] for entry in rows[0]["images"]] == [b"a", b"b", b"c", b"d"]


def test_an_image_is_embedded_as_bytes_with_no_path_for_the_viewer_to_chase() -> None:
    rows = build_dataset_rows(
        documents=[document("a")],
        images=[image("a", digest("a"))],
        image_bytes={digest("a"): b"png-bytes"},
    )

    assert rows[0]["images"] == [{"bytes": b"png-bytes", "path": None}]


def test_a_relevant_document_with_no_publishable_images_carries_an_empty_list() -> None:
    rows = build_dataset_rows(documents=[document("a")], images=[], image_bytes={})

    assert rows[0]["images"] == []


def test_a_digest_missing_from_the_bytes_mapping_contributes_no_image() -> None:
    rows = build_dataset_rows(
        documents=[document("a")],
        images=[image("a", digest("a"), image_index=0), image("a", digest("b"), image_index=1)],
        image_bytes={digest("b"): b"cleared"},
    )

    assert rows[0]["images"] == [{"bytes": b"cleared", "path": None}]


def test_images_belonging_to_another_document_never_leak_into_a_row() -> None:
    rows = build_dataset_rows(
        documents=[document("a"), document("b")],
        images=[image("a", digest("a")), image("b", digest("b"))],
        image_bytes={digest("a"): b"a", digest("b"): b"b"},
    )

    assert [[entry["bytes"] for entry in row["images"]] for row in rows] == [[b"a"], [b"b"]]


def test_building_the_same_input_twice_produces_identical_rows() -> None:
    kwargs: dict[str, Any] = {
        "documents": [document("a"), document("b", relevant=False)],
        "images": [image("a", digest("a")), image("a", digest("b"), image_index=1)],
        "image_bytes": {digest("a"): b"a", digest("b"): b"b"},
    }

    assert build_dataset_rows(**kwargs) == build_dataset_rows(**kwargs)


def test_the_card_declares_a_single_default_config_pointing_at_the_one_parquet() -> None:
    parsed = front_matter(card())

    assert parsed["configs"] == [
        {
            "config_name": CONFIG_NAME,
            "data_files": [{"split": SPLIT_NAME, "path": DATASET_FILE}],
        }
    ]


def test_the_card_declares_the_four_features_with_the_spellings_the_hub_understands() -> None:
    parsed = front_matter(card())

    assert parsed["dataset_info"]["features"] == [
        {"name": "pdf_url", "dtype": "string"},
        {"name": "text", "dtype": "string"},
        {"name": "images", "sequence": "image"},
        {"name": "matched_terms", "sequence": "string"},
    ]


def test_the_card_declares_the_odc_by_licence_the_source_dataset_requires() -> None:
    assert front_matter(card())["license"] == "odc-by"


def test_the_front_matter_features_are_generated_from_the_published_columns() -> None:
    declared = [entry["name"] for entry in front_matter(card())["dataset_info"]["features"]]

    assert declared == [name for name, _, _, _ in DATASET_FIELDS]


def test_the_card_carries_the_attribution_and_takedown_route_verbatim() -> None:
    text = card()

    assert policy.SOURCE_ATTRIBUTION in text
    assert policy.TAKEDOWN_CONTACT in text


def test_the_card_records_the_provenance_needed_to_reproduce_or_cite_the_run() -> None:
    text = card()

    for value in (SOURCE["revision"], SOURCE["config"], SOURCE["split"], SOURCE["shard"]):
        assert value in text
    assert str(SAMPLING["limit"]) in text
    assert str(SAMPLING["strategy"]) in text


def test_the_card_shows_a_reader_how_to_load_the_dataset() -> None:
    assert f'load_dataset("{REPO}", split="{SPLIT_NAME}")' in card()


def test_the_card_names_every_published_column_for_a_reader() -> None:
    text = card()

    for name, _, _, meaning in DATASET_FIELDS:
        assert f"`{name}`" in text
        assert meaning in text
    assert "`sequence[image]`" in text


def test_the_card_stays_under_the_fifty_line_budget() -> None:
    assert len(card().splitlines()) < 50


def test_the_card_drops_the_bookkeeping_the_old_one_explained_to_its_maintainers() -> None:
    text = card().lower()

    for gone in ("disposition", "license_status", "relevance_score", "row_id", "manifest"):
        assert gone not in text
