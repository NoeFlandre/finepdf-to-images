"""The published schema, the generated card, and what a publication consists of."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DOCUMENT_FIELDS,
    DOCUMENTS_FILE,
    DOCUMENTS_RELEVANT_FILE,
    DOCUMENTS_RETRIEVED_FILE,
    IMAGE_FIELDS,
    IMAGES_FILE,
    MANIFEST_FILE,
    MAX_DOCUMENT_TEXT_BYTES,
    SCHEMA_VERSION,
    PublicationError,
    build_document_rows,
    build_image_rows,
    build_manifest,
    build_plan,
    derive_relevant_rows,
    derive_retrieved_rows,
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


def scored_row(index: int, *, relevant: bool = True, text: str = "sample text") -> dict[str, Any]:
    return {
        "row_index": index,
        "row_id": f"<urn:uuid:{index:012d}>",
        "url": f"https://fixtures.invalid/{index}.pdf",
        "text": text,
        "text_sha256": sha256_hex(text.encode("utf-8")),
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
    assert manifest["splits"] == {
        "documents": {
            "all": 2,
            "relevant": 1,
            "retrieved": 1,
        },
        "images": {
            "train": 1,
        },
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


def test_a_publication_has_the_expected_files() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert [file.path for file in plan.files] == [
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
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


@pytest.mark.parametrize(
    "missing",
    [
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
        IMAGES_FILE,
    ],
)
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
    parts = card.split("---\n", 2)
    assert len(parts) >= 3, "card missing closing delimiter for YAML front matter"
    meta = yaml.safe_load(parts[1])
    assert isinstance(meta, dict)
    assert meta["license"] == "odc-by"
    assert meta["configs"] == [
        {
            "config_name": "documents",
            "data_files": [
                {"split": "all", "path": "data/documents.jsonl"},
                {"split": "relevant", "path": "data/documents_relevant.jsonl"},
                {"split": "retrieved", "path": "data/documents_retrieved.jsonl"},
            ],
        },
        {"config_name": "images", "data_files": "data/images.jsonl"},
    ]
    assert DOCUMENTS_FILE == "data/documents.jsonl"
    assert DOCUMENTS_RELEVANT_FILE == "data/documents_relevant.jsonl"
    assert DOCUMENTS_RETRIEVED_FILE == "data/documents_retrieved.jsonl"
    assert IMAGES_FILE == "data/images.jsonl"


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
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
        IMAGES_FILE,
    }
    assert not manifest["publishes_source_bytes"]


# --------------------------------------------------------------------------- content identity


def test_a_file_matches_either_identity_the_hub_may_report() -> None:
    """REGRESSION: matching on SHA-256 alone meant nothing this stage publishes ever matched.

    The Hub reports a git blob id for an ordinary file and a content SHA-256 only for an LFS
    object. All four published files are small, so on the real Hub every verification would have
    failed and every re-run would have created another commit.
    """
    from finepdf_to_images.domain.publication import PublishFile

    file = PublishFile("README.md", b"# card")
    assert file.matches(file.sha256)
    assert file.matches(file.git_blob_sha1)
    assert file.sha256 != file.git_blob_sha1
    assert not file.matches("0" * 40)
    assert not file.matches(None)


def test_the_git_blob_id_is_the_one_git_itself_would_compute() -> None:
    """Checked against git's documented object format rather than against our own function."""
    import hashlib
    import subprocess

    from finepdf_to_images.domain.publication import PublishFile

    data = b"some published bytes\n"
    expected = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
    assert PublishFile("x", data).git_blob_sha1 == expected

    real = subprocess.run(
        ["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True
    )
    assert real.stdout.decode().strip() == expected


def test_a_noop_is_recognised_from_git_blob_ids() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert is_noop(plan, {file.path: file.git_blob_sha1 for file in plan.files})


def test_a_cleared_row_is_refused_until_byte_publication_exists() -> None:
    """REGRESSION: the card's "some source bytes are republished" sentence could fire while the
    plan still contained only the index -- a published falsehood."""
    documents, images, manifest = assembled()
    documents[0]["disposition"] = "publish-artifact"
    manifest = build_manifest(
        source=SOURCE,
        sampling=SAMPLING,
        documents=documents,
        images=images,
        vocabulary_version=3,
    )
    assert manifest["publishes_source_bytes"] is True
    with pytest.raises(PublicationError, match="does not implement"):
        build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)


def test_every_published_document_row_carries_text_and_text_sha256() -> None:
    text = "Sample agricultural document text."
    digest = sha256_hex(text.encode("utf-8"))
    scored = [
        {
            "row_index": 0,
            "row_id": "row-1",
            "url": "https://example.org/1.pdf",
            "text": text,
            "text_sha256": digest,
            "relevance": {
                "relevant": True,
                "score": 1,
                "matched_terms": ["soil"],
                "language": "eng_Latn",
            },
        }
    ]
    (row,) = build_document_rows(scored=scored, retrieved=[], extracted=[])
    assert row["text"] == text
    assert row["text_sha256"] == digest


def test_published_text_must_match_recorded_text_sha256() -> None:
    scored = [
        {
            "row_index": 0,
            "row_id": "row-1",
            "url": "https://example.org/1.pdf",
            "text": "original text",
            "text_sha256": sha256_hex(b"tampered text"),
            "relevance": {},
        }
    ]
    with pytest.raises(PublicationError, match="text_sha256"):
        build_document_rows(scored=scored, retrieved=[], extracted=[])


def test_total_text_byte_cap_enforced_at_boundary() -> None:
    text_10_bytes = "0123456789"
    scored = [
        {
            "row_index": 0,
            "row_id": "row-1",
            "url": "https://example.org/1.pdf",
            "text": text_10_bytes,
            "text_sha256": sha256_hex(text_10_bytes.encode("utf-8")),
            "relevance": {},
        }
    ]
    # Exactly at boundary: passes
    rows = build_document_rows(scored=scored, retrieved=[], extracted=[], max_text_bytes=10)
    assert len(rows) == 1

    # Over boundary by 1 byte: fails
    with pytest.raises(PublicationError, match="total published text bytes"):
        build_document_rows(scored=scored, retrieved=[], extracted=[], max_text_bytes=9)


def test_build_plan_enforces_max_document_text_bytes() -> None:
    _, _, manifest = assembled()
    with pytest.raises(PublicationError, match="total published text bytes"):
        build_plan(
            repo="a/b",
            manifest=manifest,
            documents=[{"row_id": "r1", "text": "x" * (MAX_DOCUMENT_TEXT_BYTES + 1)}],
            images=[],
        )


def test_the_card_states_odc_by_attribution_obligation_for_text() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "ODC-BY" in card
    assert "HuggingFaceFW/finepdfs" in card
    assert "text" in card.lower()


@given(
    rows=st.lists(
        st.fixed_dictionaries(
            {
                "row_index": st.integers(min_value=0, max_value=1000),
                "row_id": st.text(min_size=1, max_size=20),
                "relevant": st.booleans(),
                "retrieved": st.booleans(),
            }
        ),
        max_size=30,
    )
)
def test_derived_splits_are_subsets_and_match_predicates(rows: list[dict[str, Any]]) -> None:
    docs = sorted(rows, key=lambda r: (r["row_index"] is None, r["row_index"]))
    relevant = derive_relevant_rows(docs)
    retrieved = derive_retrieved_rows(docs)

    assert len(relevant) <= len(docs)
    assert len(retrieved) <= len(docs)
    assert all(r in docs for r in relevant)
    assert all(r in docs for r in retrieved)

    assert all(r["relevant"] is True for r in relevant)
    assert len(relevant) == sum(1 for r in docs if r["relevant"])
    assert [r["row_id"] for r in relevant] == [r["row_id"] for r in docs if r["relevant"]]

    assert all(r["retrieved"] is True for r in retrieved)
    assert len(retrieved) == sum(1 for r in docs if r["retrieved"])
    assert [r["row_id"] for r in retrieved] == [r["row_id"] for r in docs if r["retrieved"]]


def test_the_card_documents_splits_and_files() -> None:
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert DOCUMENTS_RELEVANT_FILE in card
    assert DOCUMENTS_RETRIEVED_FILE in card
    assert "split: `all`" in card or "split `all`" in card or "`all`" in card
    assert "split: `relevant`" in card or "split `relevant`" in card or "`relevant`" in card
    assert "split: `retrieved`" in card or "split `retrieved`" in card or "`retrieved`" in card
