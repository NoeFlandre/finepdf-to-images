"""Tests written to kill specific surviving mutants.

Mutation testing over the pure domain surfaced three classes of survivor that mattered:

**Schema key names.** A mutant renaming ``"dataset"`` to ``"DATASET"`` in a manifest survived,
because nothing asserted the exact keys. Those key names are the published contract -- the dataset
card documents them and consumers index on them -- so a rename must fail the build, not pass it.

**Boundaries.** Mutants turning ``< 1`` into ``<= 1`` or ``>= MIN`` into ``> MIN`` survived because
only the far side of each boundary was tested. A limit that rejects a legitimate value is as much a
bug as one that accepts an illegitimate one.

**Published values.** Mutants replacing a lookup key with ``None`` -- ``image.get("sha256")``
becoming ``image.get(None)`` -- survived across both published-row builders. The rows kept every
documented key and the schema tests passed; the columns were simply empty. See the last section of
this file.

The remaining survivors are overwhelmingly diagnostic-string mutations -- upper-casing a message,
replacing it with ``None``. Those are deliberately not chased: asserting the exact prose of every
error message would make the tests a transcription of the source, and the messages are already
checked where their *content* matters (a reason a caller matches on, a field name a user needs).
"""

from __future__ import annotations

from typing import Any

import pytest

from finepdf_to_images.domain.images import ImageRecord, build_image_record, image_path
from finepdf_to_images.domain.policy import (
    LicenseDeclaration,
    decide,
    policy_summary,
)
from finepdf_to_images.domain.publication import build_document_rows, build_image_rows
from finepdf_to_images.domain.retrieval import (
    PDF_MAGIC,
    RetrievalLimits,
    evaluate,
    validate_url,
)
from finepdf_to_images.domain.scoring import (
    MIN_CONCEPTS_IN_ONE_GROUP,
    score,
    vocabulary_summary,
)
from finepdf_to_images.domain.serialization import canonical_bytes, sha256_hex
from finepdf_to_images.domain.source import (
    SamplingSpec,
    SourceRef,
    build_manifest,
    build_record,
    read_window,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


# --------------------------------------------------------------------------- schema key names


def test_the_source_reference_schema_keys_are_exact() -> None:
    """These are published in every manifest and the dataset card. A rename is a breaking change."""
    assert set(SourceRef().as_dict()) == {
        "dataset",
        "revision",
        "config",
        "split",
        "shard",
        "path",
    }


def test_the_sampling_schema_keys_are_exact() -> None:
    assert set(SamplingSpec().as_dict()) == {"limit", "seed", "strategy"}


def test_the_select_manifest_schema_keys_are_exact() -> None:
    manifest = build_manifest(
        ref=SourceRef(),
        spec=SamplingSpec(),
        records=[],
        read={"rows_fetched": 0},
    )
    assert set(manifest) == {
        "schema_version",
        "stage",
        "source",
        "sampling",
        "read",
        "counts",
        "records",
        "records_digest",
    }
    assert manifest["schema_version"] == 1
    assert manifest["stage"] == "select"


def test_the_relevance_schema_keys_are_exact() -> None:
    assert set(score("maize irrigation").as_dict()) == {
        "relevant",
        "score",
        "matched_groups",
        "evidence",
        "matched_terms",
        "concept_depth",
        "language",
        "vocabulary_language",
        "vocabulary_version",
        "language_matches_vocabulary",
    }


def test_the_vocabulary_summary_keys_are_exact() -> None:
    assert set(vocabulary_summary()) == {
        "version",
        "language",
        "groups",
        "concept_count",
        "surface_form_count",
        "thresholds",
        "excluded_ambiguous_terms",
        "weak_concepts",
    }


def test_the_policy_summary_keys_are_exact() -> None:
    assert set(policy_summary()) == {
        "default_disposition",
        "allowed_licenses",
        "trusted_evidence",
        "required_provenance",
        "required_artifact_provenance",
        "source_attribution",
        "limitations",
        "takedown",
    }


def test_the_publication_decision_keys_are_exact() -> None:
    assert set(decide({}).as_dict()) == {"disposition", "reason", "license"}
    assert set(LicenseDeclaration().as_dict()) == {"status", "identifier", "evidence", "note"}


def test_the_image_record_keys_are_exact() -> None:
    record = build_image_record(
        document_row_id="a",
        document_row_index=0,
        pdf_sha256=sha256_hex(b"pdf"),
        page_index=0,
        image_index=0,
        data=PNG,
        mime="image/png",
        width=1,
        height=1,
    )
    assert set(record.as_dict()) == {
        "document_row_id",
        "document_row_index",
        "pdf_sha256",
        "page_index",
        "image_index",
        "sha256",
        "mime",
        "width",
        "height",
        "byte_size",
        "path",
        "duplicate_of",
        "caption",
        "page_width",
        "page_height",
    }


def test_the_source_record_keys_are_exact() -> None:
    record = build_record(0, {"id": "a", "url": "https://x.invalid/a.pdf"})
    assert set(record.as_dict()) == {
        "row_index",
        "row_id",
        "url",
        "date",
        "dump",
        "language",
        "full_doc_lid",
        "full_doc_lid_score",
        "token_count",
        "is_truncated",
        "extractor",
        "text",
    }


def test_canonical_bytes_are_utf8() -> None:
    assert canonical_bytes({"crop": "blé"}) == '{"crop":"blé"}'.encode()


# --------------------------------------------------------------------------- boundaries


@pytest.mark.parametrize("timeout", [0.001, 0.5, 1.0, 1.5])
def test_a_small_positive_timeout_is_legitimate(timeout: float) -> None:
    """MUTANT: `<= 0` became `<= 1`, rejecting every timeout of a second or less."""
    assert RetrievalLimits(connect_timeout=timeout, read_timeout=timeout).connect_timeout == timeout


def test_max_bytes_exactly_at_the_header_length_is_allowed() -> None:
    """MUTANT: `<` became `<=`, rejecting the smallest coherent limit."""
    assert RetrievalLimits(max_bytes=len(PDF_MAGIC)).max_bytes == len(PDF_MAGIC)


@pytest.mark.parametrize(("redirects", "retries"), [(0, 0), (0, 1), (1, 0)])
def test_zero_redirects_and_zero_retries_are_legitimate(redirects: int, retries: int) -> None:
    """MUTANT: `< 0` became `<= 0`, so refusing to follow redirects became a configuration error."""
    limits = RetrievalLimits(max_redirects=redirects, retries=retries)
    assert (limits.max_redirects, limits.retries) == (redirects, retries)


def test_a_single_row_per_row_group_is_legitimate() -> None:
    """MUTANT: `< 1` became `< 2`, rejecting a shard with one-row groups."""
    assert read_window(3, "hash", rows_per_row_group=1) == 3


@pytest.mark.parametrize(("status", "ok"), [(199, False), (200, True), (299, True), (300, False)])
def test_the_success_status_range_is_exact(status: int, ok: bool) -> None:
    """MUTANT: `< 300` became `<= 300`, making a 300 Multiple Choices a successful retrieval."""
    record = evaluate(
        row_index=0,
        row_id="a",
        url="https://x.invalid/a.pdf",
        status=status,
        content_type="application/pdf",
        body=PDF_MAGIC + b"1.7",
        limits=RetrievalLimits(),
    )
    assert record.ok is ok


def test_exactly_the_minimum_concept_depth_is_enough() -> None:
    """MUTANT: `>=` became `>`, so the documented threshold was off by one.

    The case has to sit *exactly* on the threshold to discriminate: a four-concept document
    satisfies both `>= 3` and `> 3` and proves nothing.

    All three concepts come from the phenotyping group, so this is a single-group document and
    depth is the only thing that can make it relevant. Mixing in a crops term would satisfy the
    breadth rule instead and the depth threshold would stop being the thing under test.
    """
    result = score("Canopy cover, leaf area index and senescence were scored.")
    assert result.matched_groups == ("phenotyping",)
    assert result.concept_depth == MIN_CONCEPTS_IN_ONE_GROUP
    assert result.relevant


def test_a_document_one_concept_short_of_the_threshold_is_not_relevant() -> None:
    result = score("Canopy cover and leaf area index were scored.")
    assert result.matched_groups == ("phenotyping",)
    assert result.concept_depth == MIN_CONCEPTS_IN_ONE_GROUP - 1
    assert not result.relevant


def test_empty_text_has_zero_depth_not_one() -> None:
    """MUTANT: the `max(..., default=0)` default became 1, inventing depth for an empty document."""
    assert score("").concept_depth == 0


@pytest.mark.parametrize(
    "host",
    [
        "1.example",
        "example.2",
        "x.3com",
        # Three labels, so `labels[-1]` and `labels[1]` are different positions. A two-label host
        # cannot tell the mutant apart from the original.
        "a.b.1",
        "a.b.example",
        "deep.sub.domain.invalid",
    ],
)
def test_the_numeric_host_rule_looks_at_the_last_label(host: str) -> None:
    """MUTANT: `labels[-1]` became `labels[-2]`, checking the wrong end of the name."""
    from finepdf_to_images.domain.retrieval import UnsafeUrlError

    if host.split(".")[-1][:1].isalpha():
        assert validate_url(f"https://{host}/a.pdf").host == host
    else:
        with pytest.raises(UnsafeUrlError):
            validate_url(f"https://{host}/a.pdf")


@pytest.mark.parametrize(("width", "height"), [(1, 1), (1, 2), (2, 1)])
def test_a_one_pixel_image_is_legitimate(width: int, height: int) -> None:
    """MUTANT: `< 1` became `< 2`, rejecting a 1-pixel image -- which real PDFs contain."""
    record = build_image_record(
        document_row_id="a",
        document_row_index=0,
        pdf_sha256=sha256_hex(b"pdf"),
        page_index=0,
        image_index=0,
        data=PNG,
        mime="image/png",
        width=width,
        height=height,
    )
    assert isinstance(record, ImageRecord)
    assert (record.width, record.height) == (width, height)


def test_the_image_path_prefix_is_exact() -> None:
    digest = sha256_hex(PNG)
    assert image_path(digest, "image/png").split("/")[0] == "images"


# --------------------------------------------------------------- published row fidelity
#
# Mutation testing over the publication stage surfaced a third class of survivor, and unlike the
# two above it was not cosmetic: mutants replacing a lookup key with ``None`` -- so
# ``image.get("sha256")`` became ``image.get(None)`` -- survived across both row builders. The
# published rows still had every documented key, so the schema tests passed; the keys were just
# all empty. Nothing asserted that a published row carries the *values* the earlier stages
# produced, which is the one claim the dataset exists to make.


def _image(**overrides: object) -> dict[str, object]:
    image = {
        "document_row_id": "row-1",
        "pdf_sha256": "a" * 64,
        "page_index": 0,
        "image_index": 1,
        "sha256": "b" * 64,
        "mime": "image/png",
        "width": 12,
        "height": 34,
        "byte_size": 56,
        "duplicate_of": "a" * 64 + "#0.0",
    }
    return {**image, **overrides}


def test_every_published_image_field_carries_the_extracted_value() -> None:
    """MUTANT: ``image.get("<key>")`` -> ``image.get(None)`` in :func:`build_image_rows`.

    Survived for all ten fields. Each one is provenance -- ``row_id`` and ``pdf_sha256`` are the
    only link from an image back to the document and the PDF it came from.
    """
    (row,) = build_image_rows([_image()])
    assert row == {
        "row_id": "row-1",
        "pdf_sha256": "a" * 64,
        "page_index": 0,
        "image_index": 1,
        "sha256": "b" * 64,
        "mime": "image/png",
        "width": 12,
        "height": 34,
        "byte_size": 56,
        "duplicate_of": "a" * 64 + "#0.0",
        "image": None,
    }


def test_an_image_row_is_renamed_from_document_row_id_to_row_id() -> None:
    """The published name differs from the internal one, so the rename is load-bearing."""
    (row,) = build_image_rows([_image(document_row_id="xyz")])
    assert row["row_id"] == "xyz"
    assert "document_row_id" not in row


def test_image_rows_are_ordered_by_pdf_then_page_then_image() -> None:
    rows = build_image_rows(
        [
            _image(pdf_sha256="b" * 64, page_index=0, image_index=0),
            _image(pdf_sha256="a" * 64, page_index=2, image_index=0),
            _image(pdf_sha256="a" * 64, page_index=1, image_index=9),
            _image(pdf_sha256="a" * 64, page_index=1, image_index=2),
        ]
    )
    assert [(r["pdf_sha256"][0], r["page_index"], r["image_index"]) for r in rows] == [
        ("a", 1, 2),
        ("a", 1, 9),
        ("a", 2, 0),
        ("b", 0, 0),
    ]


def test_every_published_document_field_carries_its_stage_value() -> None:
    """MUTANT: ``_text(retrieval, "final_url")`` -> ``_text(retrieval, None)``, and eleven more.

    Each survivor emptied one published column while leaving the schema intact.
    """
    (row,) = build_document_rows(
        scored=[
            {
                "row_index": 7,
                "row_id": "row-1",
                "url": "https://example.org/a.pdf",
                "text": "soil irrigation practices",
                "text_sha256": sha256_hex(b"soil irrigation practices"),
                "relevance": {
                    "language": "eng_Latn",
                    "relevant": True,
                    "score": 5,
                    "matched_terms": ["soil", "irrigation"],
                },
            }
        ],
        retrieved=[
            {
                "row_id": "row-1",
                "ok": True,
                "final_url": "https://cdn.example.org/a.pdf",
                "reason": "unsupported-media-type",
                "sha256": "c" * 64,
                "byte_size": 2048,
                "publication": {"disposition": "metadata-only", "license": {"status": "unknown"}},
            }
        ],
        extracted=[{"row_id": "row-1", "image_count": 3}],
    )
    assert row == {
        "row_index": 7,
        "row_id": "row-1",
        "url": "https://example.org/a.pdf",
        "final_url": "https://cdn.example.org/a.pdf",
        "language": "eng_Latn",
        "text": "soil irrigation practices",
        "text_sha256": sha256_hex(b"soil irrigation practices"),
        "relevant": True,
        "relevance_score": 5,
        "matched_terms": ["soil", "irrigation"],
        "retrieved": True,
        "failure_reason": "unsupported-media-type",
        "pdf_sha256": "c" * 64,
        "pdf_bytes": 2048,
        "image_count": 3,
        "pdf": None,
        "disposition": "metadata-only",
        "license_status": "unknown",
    }


def _scored(row_id: str, row_index: int | None = 0, **overrides: Any) -> dict[str, Any]:
    """A scored row with the fields the score stage always writes.

    The minimal literals these tests used to pass omitted `text` and `url`, which the stage never
    omits. Publication now refuses a populated section that is missing a field, so a fixture that
    does not match what the pipeline produces fails -- correctly.
    """
    return {
        "row_index": row_index,
        "row_id": row_id,
        "url": f"https://fixtures.invalid/{row_id}.pdf",
        "text": "",
        "relevance": {},
        **overrides,
    }


def _retrieved(row_id: str, **overrides: Any) -> dict[str, Any]:
    """A retrieval row carrying every key the retrieve stage writes, success or failure."""
    return {
        "row_id": row_id,
        "ok": True,
        "sha256": "d" * 64,
        "byte_size": 11,
        "final_url": f"https://fixtures.invalid/{row_id}.pdf",
        "reason": None,
        "publication": {},
        **overrides,
    }


def test_a_document_row_is_joined_to_its_own_retrieval_and_extraction() -> None:
    """A key mutant that emptied the join would be invisible with a single row in play."""
    rows = build_document_rows(
        scored=[
            _scored("a", 0),
            _scored("b", 1),
        ],
        retrieved=[_retrieved("b")],
        extracted=[{"row_id": "b", "image_count": 4}],
    )
    assert [(r["row_id"], r["pdf_bytes"], r["image_count"]) for r in rows] == [
        ("a", 0, 0),
        ("b", 11, 4),
    ]


def test_a_missing_number_is_published_as_zero_not_one() -> None:
    """MUTANT: ``_number``'s fallback ``0`` -> ``1``.

    A fabricated count of 1 would be indistinguishable from a document that really did yield one
    image, so the fallback has to be the value that cannot be mistaken for a measurement.
    """
    (row,) = build_document_rows(
        scored=[_scored("a", 0)],
        retrieved=[],
        extracted=[],
    )
    assert row["pdf_bytes"] == 0
    assert row["image_count"] == 0
    assert row["relevance_score"] == 0


def test_documents_without_a_row_index_sort_after_those_that_have_one() -> None:
    """MUTANT: ``row["row_index"] is None`` -> ``is not None`` in the sort key.

    Survived because no test mixed present and absent indices. Shard order is the dataset's
    documented ordering, so a row missing its index must not displace the ordered ones.
    """
    rows = build_document_rows(
        scored=[
            _scored("n", None),
            _scored("b", 2),
            _scored("a", 1),
        ],
        retrieved=[],
        extracted=[],
    )
    assert [row["row_id"] for row in rows] == ["a", "b", "n"]
