"""Tests written to kill specific surviving mutants.

Mutation testing over the pure domain surfaced two classes of survivor that mattered:

**Schema key names.** A mutant renaming ``"dataset"`` to ``"DATASET"`` in a manifest survived,
because nothing asserted the exact keys. Those key names are the published contract -- the dataset
card documents them and consumers index on them -- so a rename must fail the build, not pass it.

**Boundaries.** Mutants turning ``< 1`` into ``<= 1`` or ``>= MIN`` into ``> MIN`` survived because
only the far side of each boundary was tested. A limit that rejects a legitimate value is as much a
bug as one that accepts an illegitimate one.

The remaining survivors are overwhelmingly diagnostic-string mutations -- upper-casing a message,
replacing it with ``None``. Those are deliberately not chased: asserting the exact prose of every
error message would make the tests a transcription of the source, and the messages are already
checked where their *content* matters (a reason a caller matches on, a field name a user needs).
"""

from __future__ import annotations

import pytest

from finepdf_to_images.domain.images import ImageRecord, build_image_record, image_path
from finepdf_to_images.domain.policy import (
    LicenseDeclaration,
    decide,
    policy_summary,
)
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
    """
    result = score("Wheat and barley cultivar trials.")
    assert result.concept_depth == MIN_CONCEPTS_IN_ONE_GROUP
    assert result.relevant


def test_a_document_one_concept_short_of_the_threshold_is_not_relevant() -> None:
    result = score("Cultivar trials for wheat.")
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
