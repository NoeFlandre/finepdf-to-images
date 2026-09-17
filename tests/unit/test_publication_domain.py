"""The published schema, the generated card, and what a publication consists of."""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.allowlist import allowlist_summary
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DOCUMENT_FIELDS,
    DOCUMENTS_FILE,
    DOCUMENTS_RELEVANT_FILE,
    DOCUMENTS_RETRIEVED_FILE,
    HUB_MANAGED_FILES,
    IMAGE_FIELDS,
    IMAGES_FILE,
    MANIFEST_FILE,
    MAX_ARTIFACT_BYTES,
    MAX_DOCUMENT_TEXT_BYTES,
    SCHEMA_VERSION,
    PublicationError,
    PublishFile,
    build_document_rows,
    build_image_rows,
    build_manifest,
    build_plan,
    cleared_image_digests,
    cleared_pdf_digests,
    cleared_row_ids,
    content_digest,
    derive_relevant_rows,
    derive_retrieved_rows,
    is_noop,
    render_card,
    stale_paths,
)
from finepdf_to_images.domain.retrieval import artifact_path
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
        "published_images": 0,
        "published_pdfs": 0,
    }
    assert manifest["splits"] == {
        "documents": {
            "all": {
                "path": DOCUMENTS_FILE,
                "count": 2,
                "digest": manifest["documents_digest"],
            },
            "relevant": {
                "path": DOCUMENTS_RELEVANT_FILE,
                "count": 1,
                "digest": manifest["documents_relevant_digest"],
            },
            "retrieved": {
                "path": DOCUMENTS_RETRIEVED_FILE,
                "count": 1,
                "digest": manifest["documents_retrieved_digest"],
            },
        },
        "images": {
            "train": {
                "path": IMAGES_FILE,
                "count": 1,
                "digest": manifest["images_digest"],
            },
        },
    }


def test_the_manifest_records_deterministic_split_digests() -> None:
    documents, images, manifest = assembled()
    relevant = derive_relevant_rows(documents)
    retrieved = derive_retrieved_rows(documents)

    assert manifest["documents_digest"] == content_digest(list(documents))
    assert manifest["documents_relevant_digest"] == content_digest(relevant)
    assert manifest["documents_retrieved_digest"] == content_digest(retrieved)
    assert manifest["images_digest"] == content_digest(list(images))

    for key in (
        "documents_digest",
        "documents_relevant_digest",
        "documents_retrieved_digest",
        "images_digest",
    ):
        digest = manifest[key]
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", digest)

    assert manifest["documents_digest"] != manifest["documents_relevant_digest"]


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
        {
            "config_name": "images",
            "data_files": [
                {"split": "train", "path": "data/images.jsonl"},
            ],
        },
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


def test_a_card_claiming_published_bytes_is_refused_without_them() -> None:
    """REGRESSION: the card's "some source bytes are republished" sentence could fire while the
    plan still contained only the index -- a published falsehood.

    This used to be guaranteed by refusing byte publication outright. Now that artifacts can
    ship, the claim and the payload are checked against each other instead."""
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
    with pytest.raises(PublicationError, match="carries no artifact"):
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


def test_the_text_cap_counts_every_file_the_plan_publishes() -> None:
    """REGRESSION: the cap measured the ``all`` set only, while the plan writes the text 3x.

    A row that is relevant and retrieved is republished in both derived splits, so its text is
    published three times. Counting it once let a publication exceed the cap threefold: with the
    pilot's real numbers the check reported 24.7 MB against 30.1 MB actually uploaded.

    Here each row's text is just over a third of the cap. One copy fits; three do not.
    """
    _, _, manifest = assembled()
    third = MAX_DOCUMENT_TEXT_BYTES // 3 + 1
    text = "x" * third
    documents = [
        {
            "row_id": "r1",
            "text": text,
            "text_sha256": sha256_hex(text.encode("utf-8")),
            "relevant": True,
            "retrieved": True,
        }
    ]
    assert len(text.encode("utf-8")) <= MAX_DOCUMENT_TEXT_BYTES, "one copy must fit under the cap"

    with pytest.raises(PublicationError, match="across 3 published document file"):
        build_plan(repo="a/b", manifest=manifest, documents=documents, images=[])


def test_a_document_in_no_split_is_counted_once_against_the_cap() -> None:
    """The inverse: a row in no split is written once, so it must not be triple-counted."""
    _, _, manifest = assembled()
    text = "x" * (MAX_DOCUMENT_TEXT_BYTES // 2)
    documents = [
        {
            "row_id": "r1",
            "text": text,
            "text_sha256": sha256_hex(text.encode("utf-8")),
            "relevant": False,
            "retrieved": False,
        }
    ]
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=[])
    assert len(plan.files) == 6


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
    # No sort: the derived splits must preserve whatever order they are given, and asserting
    # that against the generated order is the point. The sort this line used to perform keyed on
    # `row_index is None`, which the strategy never generates -- a branch that could not run.
    docs = rows
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
    assert "| `documents` | `all` | 2 |" in card
    assert "| `documents` | `relevant` | 1 |" in card
    assert "| `documents` | `retrieved` | 1 |" in card
    assert "| `images` | `train` | 1 |" in card
    assert f"| `{DOCUMENTS_FILE}` | one row per scored document (split: `all`) |" in card
    assert (
        f"| `{DOCUMENTS_RELEVANT_FILE}` | documents judged relevant to agriculture"
        " (split: `relevant`) |" in card
    )
    assert (
        f"| `{DOCUMENTS_RETRIEVED_FILE}` | documents whose source PDF was retrieved"
        " (split: `retrieved`) |" in card
    )
    assert f"- `documents` (`all`): `{manifest['documents_digest'][:16]}…`" in card
    assert f"- `documents` (`relevant`): `{manifest['documents_relevant_digest'][:16]}…`" in card
    assert f"- `documents` (`retrieved`): `{manifest['documents_retrieved_digest'][:16]}…`" in card
    assert f"- `images` (`train`): `{manifest['images_digest'][:16]}…`" in card


# --------------------------------------------------------------- publishing artifact bytes
#
# This is the only path in the project that can put a third party's work on the internet. The
# tests here are about what it refuses: the checks replaced a blanket interlock that refused all
# byte publication, so they have to be stricter than "the caller passed it in".

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
PNG_SHA = sha256_hex(PNG)
PDF = b"%PDF-1.4 body"
PDF_SHA = sha256_hex(PDF)


def _cleared_doc(**overrides: Any) -> dict[str, Any]:
    row = {
        "row_index": 0,
        "row_id": "row-1",
        "relevant": True,
        "retrieved": True,
        "pdf_sha256": PDF_SHA,
        "pdf": artifact_path(PDF_SHA),
        "disposition": "publish-artifact",
    }
    return {**row, **overrides}


def _cleared_image(**overrides: Any) -> dict[str, Any]:
    """A *published* image row -- the shape ``build_plan`` sees, with the rendered path resolved."""
    image = {
        "row_id": "row-1",
        "sha256": PNG_SHA,
        "mime": "image/png",
        "page_index": 0,
        "image_index": 0,
        "image": image_path(PNG_SHA, "image/png"),
    }
    return {**image, **overrides}


def _manifest_claiming_bytes(documents: list[dict[str, Any]]) -> dict[str, Any]:
    _, _, manifest = assembled()
    return {
        **manifest,
        "publishes_source_bytes": any(
            row.get("disposition") == "publish-artifact" for row in documents
        ),
    }


def test_a_cleared_row_publishes_its_pdf_and_image_bytes() -> None:
    documents = [_cleared_doc()]
    images = [_cleared_image()]
    plan = build_plan(
        repo="a/b",
        manifest=_manifest_claiming_bytes(documents),
        documents=documents,
        images=images,
        artifacts=[
            PublishFile(artifact_path(PDF_SHA), PDF),
            PublishFile(image_path(PNG_SHA, "image/png"), PNG),
        ],
    )
    paths = [file.path for file in plan.files]
    assert artifact_path(PDF_SHA) in paths
    assert image_path(PNG_SHA, "image/png") in paths


def test_an_artifact_for_an_uncleared_row_is_refused() -> None:
    """The whole point. Only the curated allow list can clear a row, and it did not clear this one.

    The cleared row here is a *different* document, so the manifest legitimately claims bytes are
    republished; the artifact offered belongs to the uncleared one.
    """
    documents = [
        _cleared_doc(),
        _cleared_doc(
            row_id="row-2", pdf_sha256=sha256_hex(b"other"), pdf=None, disposition="metadata-only"
        ),
    ]
    with pytest.raises(PublicationError, match="cleared for byte publication"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[
                PublishFile(artifact_path(PDF_SHA), PDF),
                PublishFile(artifact_path(sha256_hex(b"other")), b"other"),
            ],
        )


def test_an_artifact_at_a_hand_written_path_is_refused() -> None:
    """Content addressing is worthless if the path can disagree with the bytes.

    The bytes here *are* cleared, so this is not the allow-list check firing: it is the path
    check, which is what keeps a published artifact self-verifying.
    """
    documents = [_cleared_doc()]
    with pytest.raises(PublicationError, match="does not match its own bytes"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[PublishFile(f"pdfs/{PDF_SHA}.pdf", PDF)],
        )


def test_bytes_that_hash_to_an_uncleared_digest_are_refused() -> None:
    """Swapping the bytes under a cleared path does not inherit that path's clearance."""
    documents = [_cleared_doc()]
    with pytest.raises(PublicationError, match="cleared for byte publication"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[PublishFile(artifact_path(PDF_SHA), b"different bytes entirely")],
        )


def test_an_artifact_outside_the_known_prefixes_is_refused() -> None:
    documents = [_cleared_doc()]
    with pytest.raises(PublicationError, match="neither a PDF nor an image"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[PublishFile("../../etc/passwd", PDF)],
        )


def test_an_image_the_index_does_not_claim_is_refused() -> None:
    """The index and the payload are one statement: bytes for a row saying ``image: null``
    would be a file no published row points at."""
    documents = [_cleared_doc()]
    images = [_cleared_image(image=None)]
    with pytest.raises(PublicationError, match="cleared for byte publication"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=images,
            artifacts=[PublishFile(image_path(PNG_SHA, "image/png"), PNG)],
        )


def test_total_artifact_bytes_are_capped() -> None:
    oversized = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_ARTIFACT_BYTES
    digest = sha256_hex(oversized)
    documents = [_cleared_doc(pdf_sha256=digest, pdf=artifact_path(digest))]
    with pytest.raises(PublicationError, match="exceeds cap"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[PublishFile(artifact_path(digest), oversized)],
        )


def test_a_plan_with_artifacts_but_no_claim_is_refused() -> None:
    """The card and the payload must agree in both directions, not just one."""
    documents = [_cleared_doc()]
    manifest = {**_manifest_claiming_bytes(documents), "publishes_source_bytes": False}
    with pytest.raises(PublicationError, match="claims no source bytes"):
        build_plan(
            repo="a/b",
            manifest=manifest,
            documents=documents,
            images=[],
            artifacts=[PublishFile(artifact_path(PDF_SHA), PDF)],
        )


def test_an_image_row_points_at_its_published_bytes_only_when_they_ship() -> None:
    images = [_cleared_image(), _cleared_image(document_row_id="row-2", sha256="b" * 64)]
    rows = build_image_rows(images, {PNG_SHA: "image/png"})
    by_digest = {row["sha256"]: row for row in rows}
    assert by_digest[PNG_SHA]["image"] == image_path(PNG_SHA, "image/png")
    assert by_digest["b" * 64]["image"] is None


def test_cleared_helpers_read_the_policy_decision_rather_than_re_deriving_it() -> None:
    documents = [_cleared_doc(), _cleared_doc(row_id="row-2", disposition="metadata-only")]
    assert cleared_row_ids(documents) == frozenset({"row-1"})
    assert cleared_pdf_digests(documents) == {PDF_SHA: "row-1"}


def test_a_cleared_row_without_a_usable_digest_contributes_no_artifact() -> None:
    documents = [_cleared_doc(pdf_sha256="", pdf=None)]
    assert cleared_pdf_digests(documents) == {}


def test_the_card_declares_the_image_column_as_an_image() -> None:
    """Without this the viewer renders a path string instead of a picture."""
    _, _, manifest = assembled()
    front_matter = yaml.safe_load(render_card(manifest).split("---")[1])
    features = {entry["config_name"]: entry["features"] for entry in front_matter["dataset_info"]}
    typed = {feature["name"]: feature["dtype"] for feature in features["images"]}
    assert typed["image"] == "image"
    assert set(typed) == {field for field, _ in IMAGE_FIELDS}


def test_the_card_names_every_allow_listed_source_and_its_basis() -> None:
    """The allow list is the project's main standing risk, so it is published, not just applied."""
    _, _, manifest = assembled()
    card = render_card(manifest)
    for entry in manifest["allowlist"]:
        assert f"`{entry['host']}`" in card
        assert entry["basis"] in card


def test_the_manifest_records_the_allow_list() -> None:
    _, _, manifest = assembled()
    assert manifest["allowlist"] == allowlist_summary()
    assert manifest["allowlist"], "an empty allow list would make the card claim nothing is cleared"


def test_the_card_states_the_published_artifact_counts_when_bytes_ship() -> None:
    documents = [_cleared_doc()]
    base = _manifest_claiming_bytes(documents)
    manifest = {
        **base,
        "counts": {**base["counts"], "published_pdfs": 1, "published_images": 2},
    }
    card = render_card(manifest)
    assert "1 source PDF(s) and 2 image(s) are republished" in card


def test_a_row_pointing_at_bytes_the_plan_omits_is_refused() -> None:
    """A published row naming an image nobody uploaded is a 404 in the dataset viewer."""
    documents = [_cleared_doc()]
    images = [_cleared_image()]
    with pytest.raises(PublicationError, match="does not carry"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=images,
            artifacts=[PublishFile(artifact_path(PDF_SHA), PDF)],
        )


def test_cleared_image_digests_reads_the_raw_extraction_key() -> None:
    """REGRESSION: the raw index keys its owner as ``document_row_id``; published rows use
    ``row_id``. Reading the wrong one matched nothing and silently published no images."""
    images = [
        {"document_row_id": "row-1", "sha256": PNG_SHA, "mime": "image/png"},
        {"document_row_id": "row-2", "sha256": "b" * 64, "mime": "image/jpeg"},
    ]
    assert cleared_image_digests(images, frozenset({"row-1"})) == {PNG_SHA: "image/png"}


def test_cleared_image_digests_ignores_a_published_row_shape() -> None:
    """Passing published rows here is a silent no-op, so it is asserted rather than assumed."""
    assert cleared_image_digests([_cleared_image()], frozenset({"row-1"})) == {}


def test_cleared_image_digests_skips_an_unusable_digest() -> None:
    images = [{"document_row_id": "row-1", "sha256": "not-a-digest", "mime": "image/png"}]
    assert cleared_image_digests(images, frozenset({"row-1"})) == {}


def test_cleared_image_digests_keeps_one_entry_per_digest() -> None:
    """The same image on two pages is two rows and one artifact."""
    images = [
        {"document_row_id": "row-1", "sha256": PNG_SHA, "mime": "image/png", "page_index": 0},
        {"document_row_id": "row-1", "sha256": PNG_SHA, "mime": "image/png", "page_index": 7},
    ]
    assert cleared_image_digests(images, frozenset({"row-1"})) == {PNG_SHA: "image/png"}


def test_nothing_is_cleared_when_no_row_is() -> None:
    images = [{"document_row_id": "row-1", "sha256": PNG_SHA, "mime": "image/png"}]
    assert cleared_image_digests(images, frozenset()) == {}


def test_image_bytes_need_a_cleared_document_not_just_a_populated_index() -> None:
    """REGRESSION: image clearance was read from the caller's index alone.

    For PDFs the check joined to the policy's `disposition`; for images it trusted the `image`
    column the caller passed. So `build_plan` -- the documented gate, removed on the promise that
    it is *stricter* than its caller -- would have accepted image bytes for a document the policy
    never cleared. The pipeline joined correctly, so nothing was exploitable in practice, which is
    exactly why it needed a test rather than a reading.
    """
    documents = [_cleared_doc(row_id="row-1")]
    images = [_cleared_image(row_id="row-uncleared")]
    with pytest.raises(PublicationError, match="cleared for byte publication"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=images,
            artifacts=[
                PublishFile(artifact_path(PDF_SHA), PDF),
                PublishFile(image_path(PNG_SHA, "image/png"), PNG),
            ],
        )


def test_an_image_of_a_cleared_document_still_publishes() -> None:
    """The join must not be so strict that the legitimate case stops working."""
    documents = [_cleared_doc(row_id="row-1")]
    images = [_cleared_image(row_id="row-1")]
    plan = build_plan(
        repo="a/b",
        manifest=_manifest_claiming_bytes(documents),
        documents=documents,
        images=images,
        artifacts=[
            PublishFile(artifact_path(PDF_SHA), PDF),
            PublishFile(image_path(PNG_SHA, "image/png"), PNG),
        ],
    )
    assert image_path(PNG_SHA, "image/png") in {file.path for file in plan.files}


def test_the_same_artifact_twice_is_refused() -> None:
    """Counted twice against the cap and uploaded twice; neither is intended."""
    documents = [_cleared_doc()]
    with pytest.raises(PublicationError, match="repeated"):
        build_plan(
            repo="a/b",
            manifest=_manifest_claiming_bytes(documents),
            documents=documents,
            images=[],
            artifacts=[
                PublishFile(artifact_path(PDF_SHA), PDF),
                PublishFile(artifact_path(PDF_SHA), PDF),
            ],
        )


def test_the_card_does_not_overstate_what_the_allow_list_checks() -> None:
    """REGRESSION: the card claimed every host in the redirect chain is allow-list checked.

    Only the requested and final URLs are; intermediate hops are validated for SSRF safety but
    never recorded, so they cannot be. The module docstring and ADR were corrected while this
    copy -- the one that ships to the Hub and is read by outsiders -- kept the stronger claim.
    """
    _, _, manifest = assembled()
    card = render_card(manifest)
    assert "Every host in a retrieval" not in card
    # Normalised: the card hard-wraps, so the phrase can be split across lines.
    assert "requested URL and the final URL after redirects" in " ".join(card.split())


# --------------------------------------------------------------- removing what we no longer publish


def test_stale_paths_are_the_remote_files_the_plan_does_not_contain() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {file.path: file.sha256 for file in plan.files}
    remote["data/old.jsonl"] = "0" * 64
    remote["images/ab/cd/" + "a" * 64 + ".png"] = "1" * 64

    assert stale_paths(plan, remote) == ["data/old.jsonl", "images/ab/cd/" + "a" * 64 + ".png"]


def test_a_hub_managed_file_is_never_stale() -> None:
    """`.gitattributes` belongs to the Hub. Deleting it would fight it over LFS tracking."""
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert stale_paths(plan, {".gitattributes": "x"}) == []
    assert ".gitattributes" in HUB_MANAGED_FILES


def test_nothing_is_stale_when_the_remote_matches_the_plan() -> None:
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    assert stale_paths(plan, {file.path: file.sha256 for file in plan.files}) == []


def test_a_remote_holding_extra_files_is_not_a_no_op() -> None:
    """REGRESSION: `is_noop` looked only at planned files, so a publication whose entire purpose
    was removing 203 files reported "already published and identical" and removed nothing."""
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {file.path: file.git_blob_sha1 for file in plan.files}
    assert is_noop(plan, remote)

    remote["data/left-behind.jsonl"] = "0" * 40
    assert not is_noop(plan, remote)


def test_only_paths_this_stage_writes_are_candidates_for_deletion() -> None:
    """Deleting "everything the plan does not name" is the wrong default for a shared repository."""
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {
        "LICENSE": "x",
        ".gitignore": "x",
        "assets/logo.png": "x",
        ".huggingface/config.yaml": "x",
        "data/old.jsonl": "x",
        "images/ab/cd/x.png": "x",
        "pdfs/ab/cd/x.pdf": "x",
        "manifest-old.json": "x",
    }
    assert stale_paths(plan, remote) == [
        "data/old.jsonl",
        "images/ab/cd/x.png",
        "pdfs/ab/cd/x.pdf",
    ]


def test_an_unowned_remote_file_does_not_block_a_no_op() -> None:
    """A maintainer's LICENSE must not make every run look like it has work to do."""
    documents, images, manifest = assembled()
    plan = build_plan(repo="a/b", manifest=manifest, documents=documents, images=images)
    remote = {file.path: file.git_blob_sha1 for file in plan.files}
    remote["LICENSE"] = "x"
    assert is_noop(plan, remote)


# ------------------------------------------------------------------ embedding images into rows


def _one_relevant_document() -> list[dict[str, Any]]:
    return build_document_rows(
        scored=[scored_row(0, relevant=True)],
        retrieved=[retrieved_row(0)],
        extracted=[document_row(0)],
    )


def test_the_same_image_on_several_pages_is_embedded_once() -> None:
    """The old index emitted a row per occurrence; embedding that way repeats the picture."""
    from finepdf_to_images.domain.publication import build_dataset_rows

    repeated = [image_row(0, page=0), image_row(0, page=1), image_row(0, page=2)]
    for image in repeated:
        image["sha256"] = sha256_hex(b"same")
    rows = build_dataset_rows(_one_relevant_document(), repeated, {sha256_hex(b"same"): b"same"})
    assert len(rows) == 1, "one digest, one row -- the same picture is not repeated"
    assert rows[0]["image"]["bytes"] == b"same"


def test_an_image_whose_bytes_are_unavailable_drops_the_document() -> None:
    """No broken references: a row that cannot carry its picture is not published at all."""
    from finepdf_to_images.domain.publication import build_dataset_rows

    rows = build_dataset_rows(_one_relevant_document(), [image_row(0)], {})
    assert rows == []


def test_images_are_embedded_in_page_order() -> None:
    from finepdf_to_images.domain.publication import build_dataset_rows

    images = [image_row(0, page=2), image_row(0, page=0), image_row(0, page=1)]
    available = {image["sha256"]: f"page-{index}".encode() for index, image in enumerate(images)}
    rows = build_dataset_rows(_one_relevant_document(), images, available)
    assert [row["image"]["bytes"] for row in rows] == [b"page-1", b"page-2", b"page-0"]
