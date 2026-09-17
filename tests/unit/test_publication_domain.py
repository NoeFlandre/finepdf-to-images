"""The published schema, the generated card, and what a publication consists of."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DOCUMENT_FIELDS,
    DOCUMENTS_FILE,
    IMAGE_FIELDS,
    IMAGES_FILE,
    MANIFEST_FILE,
    SCHEMA_VERSION,
    PublicationError,
    build_document_rows,
    build_image_rows,
    build_manifest,
    build_plan,
    is_noop,
    render_card,
)
from finepdf_to_images.domain.serialization import sha256_hex

SOURCE = {
    "dataset": "HuggingFaceFW/finepdfs",
    "revision": "220bac3acbf07789502c621d2d33952f51ac7f86",
    "config": "eng_Latn",
    "split": "train",
    "shard": "000_00000.parquet",
    "path": "data/eng_Latn/train/000_00000.parquet",
}
SAMPLING = {"limit": 20, "seed": "finepdf-to-images/v1", "strategy": "head"}


def scored_row(index: int, *, relevant: bool = True) -> dict[str, Any]:
    return {
        "row_index": index,
        "row_id": f"<urn:uuid:{index:012d}>",
        "url": f"https://fixtures.invalid/{index}.pdf",
        "relevance": {
            "relevant": relevant,
            "score": 3 if relevant else 0,
            "matched_terms": ["maize", "irrigation"] if relevant else [],
            "language": "eng_Latn",
        },
    }


def retrieved_row(index: int, *, ok: bool = True) -> dict[str, Any]:
    digest = sha256_hex(f"pdf-{index}".encode())
    return {
        "row_index": index,
        "row_id": f"<urn:uuid:{index:012d}>",
        "url": f"https://fixtures.invalid/{index}.pdf",
        "final_url": f"https://fixtures.invalid/{index}.pdf",
        "ok": ok,
        "reason": None if ok else "not-pdf",
        "sha256": digest if ok else None,
        "byte_size": 1234 if ok else None,
        "publication": {
            "disposition": "metadata-only",
            "reason": "redistribution not established",
            "license": {"status": "unknown", "identifier": None, "evidence": "none", "note": ""},
        },
    }


def document_row(index: int, images: int = 2) -> dict[str, Any]:
    return {
        "row_id": f"<urn:uuid:{index:012d}>",
        "pdf_sha256": sha256_hex(f"pdf-{index}".encode()),
        "image_count": images,
        "ok": True,
        "error": "",
    }


def image_row(index: int, page: int = 0, position: int = 0) -> dict[str, Any]:
    return {
        "document_row_id": f"<urn:uuid:{index:012d}>",
        "pdf_sha256": sha256_hex(f"pdf-{index}".encode()),
        "page_index": page,
        "image_index": position,
        "sha256": sha256_hex(f"img-{index}-{page}-{position}".encode()),
        "mime": "image/png",
        "width": 4,
        "height": 4,
        "byte_size": 99,
        "duplicate_of": None,
    }


def assembled(
    *, scored: list[dict[str, Any]] | None = None, images: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scored = scored if scored is not None else [scored_row(0), scored_row(1, relevant=False)]
    documents = build_document_rows(
        scored=scored, retrieved=[retrieved_row(0)], extracted=[document_row(0)]
    )
    image_rows = build_image_rows(images if images is not None else [image_row(0)])
    manifest = build_manifest(
        source=SOURCE,
        sampling=SAMPLING,
        documents=documents,
        images=image_rows,
        vocabulary_version=3,
    )
    return documents, image_rows, manifest


# --------------------------------------------------------------------------- document rows


def test_every_scored_row_is_published_not_only_the_retrieved_ones() -> None:
    """A dataset that drops its failures cannot be used to reproduce the run."""
    documents, _, _ = assembled()
    assert len(documents) == 2
    assert [row["relevant"] for row in documents] == [True, False]
    assert documents[1]["retrieved"] is False


def test_a_published_row_carries_its_full_provenance_and_evidence() -> None:
    documents, _, _ = assembled()
    row = documents[0]
    assert row["row_id"].startswith("<urn:uuid:")
    assert row["url"]
    assert row["matched_terms"] == ["maize", "irrigation"]
    assert len(row["pdf_sha256"]) == 64
    assert row["image_count"] == 2
    assert row["disposition"] == "metadata-only"
    assert row["license_status"] == "unknown"


def test_a_failed_retrieval_publishes_its_reason() -> None:
    documents = build_document_rows(
        scored=[scored_row(0)], retrieved=[retrieved_row(0, ok=False)], extracted=[]
    )
    assert documents[0]["retrieved"] is False
    assert documents[0]["failure_reason"] == "not-pdf"
    assert documents[0]["pdf_sha256"] == ""


def test_document_rows_follow_shard_order() -> None:
    documents = build_document_rows(
        scored=[scored_row(2), scored_row(0), scored_row(1)], retrieved=[], extracted=[]
    )
    assert [row["row_index"] for row in documents] == [0, 1, 2]


def test_image_rows_are_ordered_by_document_then_page_then_position() -> None:
    rows = build_image_rows([image_row(0, 1, 0), image_row(0, 0, 1), image_row(0, 0, 0)])
    assert [(r["page_index"], r["image_index"]) for r in rows] == [(0, 0), (0, 1), (1, 0)]


@pytest.mark.parametrize("field", [name for name, _ in DOCUMENT_FIELDS])
def test_every_documented_document_field_is_actually_emitted(field: str) -> None:
    """The card's schema table is generated from this list; a gap would be a published lie."""
    documents, _, _ = assembled()
    assert field in documents[0]


@pytest.mark.parametrize("field", [name for name, _ in IMAGE_FIELDS])
def test_every_documented_image_field_is_actually_emitted(field: str) -> None:
    _, images, _ = assembled()
    assert field in images[0]


# --------------------------------------------------------------------------- manifest


def test_the_manifest_records_source_sampling_and_counts() -> None:
    _, _, manifest = assembled()
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["source"] == SOURCE
    assert manifest["sampling"] == SAMPLING
    assert manifest["counts"] == {
        "documents": 2,
        "relevant": 1,
        "retrieved": 1,
        "images": 1,
        "unique_images": 1,
    }


def test_the_manifest_says_whether_source_bytes_are_republished() -> None:
    """The pilot ships no allow-list entries, so this must be false and the card must say so."""
    _, _, manifest = assembled()
    assert manifest["publishes_source_bytes"] is False


def test_the_manifest_carries_the_policy_so_the_card_cannot_drift() -> None:
    _, _, manifest = assembled()
    assert manifest["policy"]["default_disposition"] == "metadata-only"
    assert "ODC-BY" in manifest["policy"]["source_attribution"]


def test_the_manifest_has_no_timestamp() -> None:
    """A clock reading would make the idempotency claim depend on when you run it."""
    _, _, manifest = assembled()
    flat = str(manifest).lower()
    for word in ("timestamp", "generated_at", "published_at", "date"):
        assert word not in flat


# --------------------------------------------------------------------------- plan


def test_a_plan_contains_exactly_the_four_published_files() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert [file.path for file in plan.files] == [
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        IMAGES_FILE,
    ]
    assert all(file.size > 0 for file in plan.files)


def test_a_plan_is_byte_identical_for_the_same_inputs() -> None:
    documents, images, manifest = assembled()
    first = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    second = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert first.digest == second.digest
    assert [f.sha256 for f in first.files] == [f.sha256 for f in second.files]


def test_a_different_result_changes_the_plan_identity() -> None:
    documents, images, manifest = assembled()
    other_docs, other_images, other_manifest = assembled(scored=[scored_row(0)])
    a = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    b = build_plan(repo="a/b", manifest=other_manifest, documents=other_docs, images=other_images)
    assert a.digest != b.digest


@pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "/b", "a/"])
def test_an_invalid_destination_is_refused(repo: str) -> None:
    documents, images, manifest = assembled()
    with pytest.raises(PublicationError):
        build_plan(repo=repo, manifest=manifest, documents=documents, images=images)


# --------------------------------------------------------------------------- idempotency


def test_a_publication_is_a_noop_when_every_file_already_matches() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {file.path: file.sha256 for file in plan.files}
    assert is_noop(plan, remote)


@pytest.mark.parametrize("missing", [CARD_FILE, MANIFEST_FILE, DOCUMENTS_FILE, IMAGES_FILE])
def test_one_missing_or_changed_file_is_not_a_noop(missing: str) -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {file.path: file.sha256 for file in plan.files}
    del remote[missing]
    assert not is_noop(plan, remote)
    remote[missing] = "0" * 64
    assert not is_noop(plan, remote)


def test_an_empty_destination_is_never_a_noop() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert not is_noop(plan, {})


# --------------------------------------------------------------------------- the card


def test_the_card_states_that_no_source_bytes_are_republished() -> None:
    """The single most important sentence in it, and it is generated, not typed."""
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "No source PDF or image bytes are republished" in card


def test_the_card_carries_the_pinned_source_revision() -> None:
    _, _, manifest = assembled()
    assert SOURCE["revision"] in render_card(manifest)


def test_the_card_reports_the_real_counts() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "| documents scored | 2 |" in card
    assert "| judged relevant | 1 |" in card


def test_the_card_documents_every_published_field() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    for name, _description in DOCUMENT_FIELDS + IMAGE_FIELDS:
        assert f"`{name}`" in card


def test_the_card_comes_from_the_policy_rather_than_restating_it() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert manifest["policy"]["takedown"] in card
    assert manifest["policy"]["limitations"] in card
    assert manifest["policy"]["source_attribution"] in card


def test_the_card_does_not_claim_broader_coverage() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "Not a sample of" in card
    assert "proof of concept" in card.lower()
    assert "English vocabulary only" in card


def test_the_card_names_the_limits_that_matter_for_reuse() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "no OCR" in card or "no page rendering" in card
    assert "not portable across machines" in card
    assert "Retrieval is not" in card


def test_the_card_has_valid_yaml_front_matter() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert card.startswith("---\n")
    assert card.count("\n---\n") >= 1
    assert "license: odc-by" in card


def test_the_card_is_deterministic() -> None:
    _, _, manifest = assembled()
    assert render_card(manifest) == render_card(manifest)


# --------------------------------------------------------------------------- properties


@given(
    count=st.integers(min_value=0, max_value=25),
    relevant_every=st.integers(min_value=1, max_value=4),
)
def test_the_published_rows_are_exactly_the_scored_rows(count: int, relevant_every: int) -> None:
    scored = [scored_row(i, relevant=i % relevant_every == 0) for i in range(count)]
    documents = build_document_rows(scored=scored, retrieved=[], extracted=[])
    assert len(documents) == count
    assert [row["row_id"] for row in documents] == [row["row_id"] for row in scored]


@given(count=st.integers(min_value=0, max_value=20))
def test_a_plan_never_contains_source_bytes(count: int) -> None:
    """The load-bearing property: nothing this stage uploads is a third-party document."""
    scored = [scored_row(i) for i in range(count)]
    documents = build_document_rows(
        scored=scored, retrieved=[retrieved_row(i) for i in range(count)], extracted=[]
    )
    images = build_image_rows([])
    manifest = build_manifest(
        source=SOURCE,
        sampling=SAMPLING,
        documents=documents,
        images=images,
        vocabulary_version=3,
    )
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert {file.path for file in plan.files} == {
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        IMAGES_FILE,
    }
    assert not manifest["publishes_source_bytes"]
