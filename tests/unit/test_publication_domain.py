"""The published schema, the generated card, and what a publication consists of."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.allowlist import allowlist_summary
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication import (
    PublicationError,
    PublishFile,
    build_document_rows,
    build_image_rows,
    build_manifest,
    cleared_pdf_digests,
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


# --------------------------------------------------------------------------- manifest


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


# --------------------------------------------------------------------------- idempotency


# --------------------------------------------------------------------------- the card


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


# --------------------------------------------------------------------------- content identity


def test_a_file_matches_either_identity_the_hub_may_report() -> None:
    """REGRESSION: matching on SHA-256 alone meant nothing this stage publishes ever matched.

    The Hub reports a git blob id for an ordinary file and a content SHA-256 only for an LFS
    object. All four published files are small, so on the real Hub every verification would have
    failed and every re-run would have created another commit.
    """

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

    data = b"some published bytes\n"
    expected = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
    assert PublishFile("x", data).git_blob_sha1 == expected

    real = subprocess.run(
        ["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True
    )
    assert real.stdout.decode().strip() == expected


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
    """Re-pointed from build_document_rows: the cap now measures the rows actually published."""
    from finepdf_to_images.domain.publication import check_text_byte_cap

    rows = [{"text": "0123456789"}]
    check_text_byte_cap(rows, max_text_bytes=10)  # exactly at the boundary: passes

    with pytest.raises(PublicationError, match="over the cap"):
        check_text_byte_cap(rows, max_text_bytes=9)


def test_the_cap_ignores_text_the_run_never_publishes() -> None:
    """REGRESSION: a 5000-row sample was refused over 75 MB, almost all of it rejected documents.

    The cap summed every *scored* row. Once only documents that were retrieved and yielded an
    image reach the Hub, that counted text the publication never writes.
    """
    from finepdf_to_images.domain.publication import check_text_byte_cap

    scored_but_unpublished = [{"text": "x" * 1000} for _ in range(100)]
    published = [{"text": "y" * 10}]
    assert sum(len(r["text"]) for r in scored_but_unpublished) > 50
    check_text_byte_cap(published, max_text_bytes=50)


def test_the_cap_counts_text_repeated_across_a_documents_images() -> None:
    """One row per image repeats a document's text, and the cap must see every copy."""
    from finepdf_to_images.domain.publication import check_text_byte_cap

    rows = [{"text": "0123456789"} for _ in range(3)]
    with pytest.raises(PublicationError, match="counts every"):
        check_text_byte_cap(rows, max_text_bytes=25)


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


def _manifest_claiming_bytes(documents: list[dict[str, Any]]) -> dict[str, Any]:
    _, _, manifest = assembled()
    return {
        **manifest,
        "publishes_source_bytes": any(
            row.get("disposition") == "publish-artifact" for row in documents
        ),
    }


def _cleared_image(**overrides: Any) -> dict[str, Any]:
    """One published image row, with its rendered path already resolved."""
    image = {
        "row_id": "row-1",
        "sha256": PNG_SHA,
        "mime": "image/png",
        "page_index": 0,
        "image_index": 0,
        "image": image_path(PNG_SHA, "image/png"),
    }
    return {**image, **overrides}


def test_an_image_row_points_at_its_published_bytes_only_when_they_ship() -> None:
    images = [_cleared_image(), _cleared_image(document_row_id="row-2", sha256="b" * 64)]
    rows = build_image_rows(images, {PNG_SHA: "image/png"})
    by_digest = {row["sha256"]: row for row in rows}
    assert by_digest[PNG_SHA]["image"] == image_path(PNG_SHA, "image/png")
    assert by_digest["b" * 64]["image"] is None


def test_a_cleared_row_without_a_usable_digest_contributes_no_artifact() -> None:
    documents = [_cleared_doc(pdf_sha256="", pdf=None)]
    assert cleared_pdf_digests(documents) == {}


def test_the_manifest_records_the_allow_list() -> None:
    _, _, manifest = assembled()
    assert manifest["allowlist"] == allowlist_summary()
    assert manifest["allowlist"], "an empty allow list would make the card claim nothing is cleared"


# --------------------------------------------------------------- removing what we no longer publish


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


# ------------------------------------------------ stage boundaries are read strictly (#51)


def test_a_renamed_field_is_refused_rather_than_published_as_an_empty_column() -> None:
    """REGRESSION: the readers coerced anything unexpected to "", 0 or {}.

    A renamed or misspelled key therefore became a plausible published value -- an empty column in
    a public dataset, exit code 0. Mutation testing found exactly this: mutants replacing a lookup
    key survived because "the rows kept every documented key and the columns were simply empty".
    """
    scored = [
        {
            "row_index": 0,
            "row_id": "a",
            "url": "https://fixtures.invalid/a.pdf",
            "text": "",
            "relevance": {},
        }
    ]
    retrieved = [
        {
            "row_id": "a",
            "ok": True,
            "sha256": "d" * 64,
            # byte_size renamed, as a stage rename or a typo would do
            "bytes_size": 11,
            "final_url": "https://fixtures.invalid/a.pdf",
            "reason": None,
            "publication": {},
        }
    ]
    with pytest.raises(PublicationError, match="missing 'byte_size'"):
        build_document_rows(scored=scored, retrieved=retrieved, extracted=[])


def test_the_refusal_names_the_field_and_what_the_row_actually_carries() -> None:
    """A diagnostic that names the key and the row's real keys is what makes this debuggable."""
    scored = [{"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "relevance": {}}]
    with pytest.raises(PublicationError) as caught:
        build_document_rows(scored=scored, retrieved=[], extracted=[])
    message = str(caught.value)
    assert "missing 'text'" in message
    assert "relevance" in message and "row_id" in message


def test_a_field_of_the_wrong_type_is_refused() -> None:
    """A number where a string belongs is a stage contract break, not something to coerce."""
    scored = [
        {"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "text": 42, "relevance": {}}
    ]
    with pytest.raises(PublicationError, match="'text' as int, expected str"):
        build_document_rows(scored=scored, retrieved=[], extracted=[])


def test_a_document_that_was_never_retrieved_is_still_published() -> None:
    """The legitimate case the strictness must not break.

    A scored document with no retrieval and no extraction block is the pipeline working: most
    scored rows are never fetched. An absent section defaults; only a *populated* one is required
    to be complete.
    """
    scored = [
        {"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "text": "", "relevance": {}}
    ]
    (row,) = build_document_rows(scored=scored, retrieved=[], extracted=[])
    assert row["retrieved"] is False
    assert row["pdf_bytes"] == 0
    assert row["failure_reason"] == ""


def test_an_optional_field_written_as_null_reads_as_absent() -> None:
    """A successful retrieval writes reason: null rather than dropping the key."""
    scored = [
        {"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "text": "", "relevance": {}}
    ]
    retrieved = [
        {
            "row_id": "a",
            "ok": True,
            "sha256": "d" * 64,
            "byte_size": 11,
            "final_url": "https://x/a.pdf",
            "reason": None,
            "publication": {},
        }
    ]
    (row,) = build_document_rows(scored=scored, retrieved=retrieved, extracted=[])
    assert row["failure_reason"] == ""
    assert row["retrieved"] is True


def test_a_renamed_boolean_is_refused_too() -> None:
    """`retrieved` came from a bare .get(), so a renamed `ok` silently published False.

    Booleans need their own reader: `bool` is a subclass of `int`, so an int-typed check would
    accept `True` and a bool-typed one would accept `1`.
    """
    scored = [
        {"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "text": "", "relevance": {}}
    ]
    retrieved = [
        {
            "row_id": "a",
            "was_ok": True,  # renamed from `ok`
            "sha256": "d" * 64,
            "byte_size": 11,
            "final_url": "https://x/a.pdf",
            "reason": None,
            "publication": {},
        }
    ]
    with pytest.raises(PublicationError, match="missing 'ok'"):
        build_document_rows(scored=scored, retrieved=retrieved, extracted=[])


def test_a_number_where_a_boolean_belongs_is_refused() -> None:
    scored = [
        {"row_index": 0, "row_id": "a", "url": "https://x/a.pdf", "text": "", "relevance": {}}
    ]
    retrieved = [
        {
            "row_id": "a",
            "ok": 1,
            "sha256": "d" * 64,
            "byte_size": 11,
            "final_url": "https://x/a.pdf",
            "reason": None,
            "publication": {},
        }
    ]
    with pytest.raises(PublicationError, match="'ok' as int, expected bool"):
        build_document_rows(scored=scored, retrieved=retrieved, extracted=[])


def test_a_renamed_matched_terms_is_refused_rather_than_published_empty() -> None:
    """matched_terms is the column that makes the selection arguable; empty is not a value."""
    scored = [
        {
            "row_index": 0,
            "row_id": "a",
            "url": "https://x/a.pdf",
            "text": "",
            "relevance": {"relevant": True, "score": 2, "language": "en", "terms": ["maize"]},
        }
    ]
    with pytest.raises(PublicationError, match="missing 'matched_terms'"):
        build_document_rows(scored=scored, retrieved=[], extracted=[])
