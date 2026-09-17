"""The publication policy.

The property that matters most is negative: **no input can turn an unknown licence into permission
to redistribute bytes.** Most of this file exists to attack that from every direction.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.policy import (
    ALLOWED_LICENSES,
    ARTIFACT_PROVENANCE,
    REQUIRED_PROVENANCE,
    Disposition,
    EvidenceSource,
    LicenseDeclaration,
    LicenseStatus,
    decide,
    missing_provenance,
    policy_summary,
)

COMPLETE_PROVENANCE: dict[str, Any] = {
    "dataset": "HuggingFaceFW/finepdfs",
    "revision": "220bac3acbf07789502c621d2d33952f51ac7f86",
    "config": "eng_Latn",
    "split": "train",
    "shard": "000_00000.parquet",
    "row_index": 0,
    "row_id": "<urn:uuid:becf8a10-92d9-4f68-b6b4-5790712646a4>",
    "url": "https://example.invalid/a.pdf",
    "date": "2023-01-30T22:07:32+00:00",
}
"""``date`` is carried but not required: see the crawl-metadata test below."""

ARTIFACT_PROV: dict[str, Any] = {
    **COMPLETE_PROVENANCE,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}

CLEARED = LicenseDeclaration(
    status=LicenseStatus.DECLARED_OPEN,
    identifier="CC-BY-4.0",
    evidence=EvidenceSource.CURATED_ALLOWLIST,
    note="checked by hand",
)


# --------------------------------------------------------------------------- provenance


def test_complete_provenance_is_accepted() -> None:
    assert missing_provenance(COMPLETE_PROVENANCE) == []


@pytest.mark.parametrize("field", REQUIRED_PROVENANCE)
def test_every_required_field_is_actually_required(field: str) -> None:
    incomplete = {key: value for key, value in COMPLETE_PROVENANCE.items() if key != field}
    assert missing_provenance(incomplete) == [field]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_string_is_missing_not_present(blank: str) -> None:
    assert missing_provenance({**COMPLETE_PROVENANCE, "url": blank}) == ["url"]


def test_row_index_zero_is_a_real_value_not_an_absence() -> None:
    """A falsiness check here would silently exclude the shard's first row."""
    assert missing_provenance({**COMPLETE_PROVENANCE, "row_index": 0}) == []


def test_untraceable_artifact_is_excluded_entirely() -> None:
    decision = decide({**COMPLETE_PROVENANCE, "row_id": None}, CLEARED)
    assert decision.disposition is Disposition.EXCLUDE
    assert not decision.publishes_bytes
    assert "row_id" in decision.reason


def test_incomplete_provenance_outranks_even_a_cleared_licence() -> None:
    """Order matters: a perfect licence does not make an untraceable row publishable."""
    assert decide({}, CLEARED).disposition is Disposition.EXCLUDE


# --------------------------------------------------------------------------- the positive case


def test_documented_compatible_permission_publishes_and_retains_attribution() -> None:
    decision = decide(COMPLETE_PROVENANCE, CLEARED)
    assert decision.disposition is Disposition.PUBLISH_ARTIFACT
    assert decision.publishes_bytes
    assert decision.license.identifier == "CC-BY-4.0"
    assert decision.license.note == "checked by hand"


@pytest.mark.parametrize("identifier", sorted(ALLOWED_LICENSES))
def test_every_allow_listed_licence_publishes(identifier: str) -> None:
    declaration = LicenseDeclaration(
        status=LicenseStatus.DECLARED_OPEN,
        identifier=identifier,
        evidence=EvidenceSource.CURATED_ALLOWLIST,
    )
    assert decide(COMPLETE_PROVENANCE, declaration).publishes_bytes


# --------------------------------------------------------------------------- the refusals


def test_no_declaration_at_all_is_metadata_only() -> None:
    decision = decide(COMPLETE_PROVENANCE)
    assert decision.disposition is Disposition.METADATA_ONLY
    assert not decision.publishes_bytes


@pytest.mark.parametrize(
    "status",
    [
        LicenseStatus.UNKNOWN,
        LicenseStatus.ABSENT,
        LicenseStatus.DECLARED_RESTRICTED,
        LicenseStatus.INCOMPATIBLE,
    ],
)
def test_anything_short_of_declared_open_is_metadata_only(status: LicenseStatus) -> None:
    declaration = LicenseDeclaration(
        status=status, identifier="CC-BY-4.0", evidence=EvidenceSource.CURATED_ALLOWLIST
    )
    assert decide(COMPLETE_PROVENANCE, declaration).disposition is Disposition.METADATA_ONLY


@pytest.mark.parametrize(
    "evidence",
    [EvidenceSource.NONE, EvidenceSource.URL_HEURISTIC, EvidenceSource.RESPONSE_HEADER],
)
def test_untrusted_evidence_cannot_publish_bytes(evidence: EvidenceSource) -> None:
    """A licence string in a response header says what a server sent, not what is permitted."""
    declaration = LicenseDeclaration(
        status=LicenseStatus.DECLARED_OPEN, identifier="CC-BY-4.0", evidence=evidence
    )
    decision = decide(COMPLETE_PROVENANCE, declaration)
    assert decision.disposition is Disposition.METADATA_ONLY
    assert "not trusted evidence" in decision.reason


@pytest.mark.parametrize(
    "identifier",
    [None, "", "CC-BY-NC-4.0", "GPL-3.0-only", "proprietary", "cc-by-4.0", " CC-BY-4.0 "],
)
def test_a_licence_off_the_allow_list_cannot_publish_bytes(identifier: str | None) -> None:
    """Including near-misses: casing and whitespace must not smuggle an entry past the list."""
    declaration = LicenseDeclaration(
        status=LicenseStatus.DECLARED_OPEN,
        identifier=identifier,
        evidence=EvidenceSource.CURATED_ALLOWLIST,
    )
    assert decide(COMPLETE_PROVENANCE, declaration).disposition is Disposition.METADATA_ONLY


def test_an_empty_allow_list_publishes_nothing() -> None:
    assert not decide(COMPLETE_PROVENANCE, CLEARED, allowed_licenses=frozenset()).publishes_bytes


# --------------------------------------------------------------------------- regressions


@pytest.mark.parametrize("allowed", ["CC-BY-4.0", ["CC-BY-4.0"], ("CC-BY-4.0",), b"CC-BY-4.0"])
def test_allow_list_must_be_a_set_not_a_sequence(allowed: object) -> None:
    """REGRESSION: str is a Sequence[str], so allowed_licenses="CC-BY-4.0" made the allow list a
    set of single characters and an identifier of "C" published."""
    with pytest.raises(TypeError, match="must be a set"):
        # ty: ignore[invalid-argument-type]
        decide(COMPLETE_PROVENANCE, CLEARED, allowed_licenses=allowed)


def test_a_single_character_identifier_cannot_slip_through_a_string_allow_list() -> None:
    declaration = LicenseDeclaration(
        status=LicenseStatus.DECLARED_OPEN,
        identifier="C",
        evidence=EvidenceSource.CURATED_ALLOWLIST,
    )
    assert not decide(COMPLETE_PROVENANCE, declaration).publishes_bytes


@pytest.mark.parametrize("value", [False, True, 0, 1, [], {}, b"", b"x", 1.5, object()])
def test_a_non_string_url_is_missing_not_present(value: object) -> None:
    """REGRESSION: only None and blank strings were rejected, so url=False read as traceable."""
    assert missing_provenance({**COMPLETE_PROVENANCE, "url": value}) == ["url"]


@pytest.mark.parametrize("value", [None, True, False, "0", 1.5, -1, [], b"0"])
def test_row_index_must_be_a_whole_non_negative_int(value: object) -> None:
    assert missing_provenance({**COMPLETE_PROVENANCE, "row_index": value}) == ["row_index"]


def test_a_raw_string_status_is_refused_at_construction() -> None:
    with pytest.raises(TypeError, match="LicenseStatus"):
        LicenseDeclaration(status="declared-open")  # ty: ignore[invalid-argument-type]


def test_a_raw_string_evidence_is_refused_at_construction() -> None:
    """REGRESSION: StrEnum members compare equal to their values, so a raw "curated-allowlist"
    string passed the evidence gate while a raw status string correctly failed."""
    with pytest.raises(TypeError, match="EvidenceSource"):
        LicenseDeclaration(evidence="curated-allowlist")  # ty: ignore[invalid-argument-type]


# --------------------------------------------------------------------------- artifact rows


def test_an_artifact_row_must_carry_its_digest() -> None:
    """metadata-only only means something if the metadata says which bytes it stands for."""
    decision = decide(COMPLETE_PROVENANCE, require_artifact_hash=True)
    assert decision.disposition is Disposition.EXCLUDE
    assert "sha256" in decision.reason


def test_an_artifact_row_with_its_digest_is_published_as_metadata() -> None:
    assert (
        decide(ARTIFACT_PROV, require_artifact_hash=True).disposition is Disposition.METADATA_ONLY
    )


def test_artifact_provenance_extends_the_base_requirement() -> None:
    assert set(REQUIRED_PROVENANCE) < set(ARTIFACT_PROVENANCE)
    assert "sha256" in ARTIFACT_PROVENANCE


def test_crawl_and_extraction_metadata_travel_without_gating_publication() -> None:
    """They are carried on SourceRecord, but a document's redistribution status does not depend
    on when it was crawled or how well its text was extracted, so they are not required here."""
    assert "date" not in REQUIRED_PROVENANCE
    assert "extractor" not in REQUIRED_PROVENANCE
    assert missing_provenance({**COMPLETE_PROVENANCE, "date": ""}) == []


@pytest.mark.parametrize(
    "digest",
    [
        "not-a-hash",
        "",
        "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855a",
        "g3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ],
)
def test_an_artifact_digest_must_actually_be_a_sha256(digest: str) -> None:
    """Presence alone is not enough: "not-a-hash" identifies no bytes at all."""
    decision = decide({**ARTIFACT_PROV, "sha256": digest}, CLEARED, require_artifact_hash=True)
    assert decision.disposition is Disposition.EXCLUDE


@pytest.mark.parametrize("field", ["url", "row_id", "dataset"])
def test_surrounding_whitespace_is_refused_not_trimmed(field: str) -> None:
    padded = f"  {COMPLETE_PROVENANCE[field]}  "
    assert missing_provenance({**COMPLETE_PROVENANCE, field: padded}) == [field]


def test_a_licence_declaration_round_trips_through_its_serialized_form() -> None:
    assert LicenseDeclaration.from_dict(CLEARED.as_dict()) == CLEARED


def test_round_trip_rejects_an_unknown_enum_value() -> None:
    with pytest.raises(ValueError):
        LicenseDeclaration.from_dict({**CLEARED.as_dict(), "evidence": "trust-me"})


# --------------------------------------------------------------------------- the core property


@pytest.mark.property
@given(
    status=st.sampled_from(list(LicenseStatus)),
    evidence=st.sampled_from(list(EvidenceSource)),
    identifier=st.one_of(
        st.none(), st.text(max_size=30), st.sampled_from(sorted(ALLOWED_LICENSES))
    ),
)
def test_bytes_are_published_only_when_all_three_conditions_hold(
    status: LicenseStatus, evidence: EvidenceSource, identifier: str | None
) -> None:
    """The load-bearing invariant: publication cannot be reached by any other combination."""
    declaration = LicenseDeclaration(status=status, identifier=identifier, evidence=evidence)
    decision = decide(COMPLETE_PROVENANCE, declaration)
    expected = (
        status is LicenseStatus.DECLARED_OPEN
        and evidence is EvidenceSource.CURATED_ALLOWLIST
        and identifier in ALLOWED_LICENSES
    )
    assert decision.publishes_bytes is expected


@pytest.mark.property
@given(
    status=st.sampled_from(list(LicenseStatus)),
    evidence=st.sampled_from(list(EvidenceSource)),
    identifier=st.one_of(st.none(), st.text(max_size=30)),
    dropped=st.sampled_from(REQUIRED_PROVENANCE),
)
def test_incomplete_provenance_never_publishes_anything(
    status: LicenseStatus, evidence: EvidenceSource, identifier: str | None, dropped: str
) -> None:
    provenance = {key: value for key, value in COMPLETE_PROVENANCE.items() if key != dropped}
    decision = decide(
        provenance, LicenseDeclaration(status=status, identifier=identifier, evidence=evidence)
    )
    assert decision.disposition is Disposition.EXCLUDE


@pytest.mark.property
@given(
    status=st.sampled_from([s for s in LicenseStatus if s is not LicenseStatus.DECLARED_OPEN]),
    evidence=st.sampled_from(list(EvidenceSource)),
    identifier=st.sampled_from(sorted(ALLOWED_LICENSES)),
)
def test_an_unknown_licence_is_never_upgraded_by_an_allow_listed_identifier(
    status: LicenseStatus, evidence: EvidenceSource, identifier: str
) -> None:
    """Carrying a good identifier alongside an unestablished status must change nothing."""
    declaration = LicenseDeclaration(status=status, identifier=identifier, evidence=evidence)
    assert not decide(COMPLETE_PROVENANCE, declaration).publishes_bytes


# --------------------------------------------------------------------------- determinism & summary


def test_the_decision_is_deterministic() -> None:
    assert decide(COMPLETE_PROVENANCE, CLEARED) == decide(COMPLETE_PROVENANCE, CLEARED)


def test_the_decision_serializes_with_its_reason() -> None:
    serialized = decide(COMPLETE_PROVENANCE).as_dict()
    assert serialized["disposition"] == "metadata-only"
    assert serialized["reason"]
    assert serialized["license"]["status"] == "unknown"


def test_the_summary_states_the_conservative_default() -> None:
    summary = policy_summary()
    assert summary["required_artifact_provenance"][-1] == "sha256"
    assert summary["default_disposition"] == "metadata-only"
    assert summary["trusted_evidence"] == ["curated-allowlist"]
    assert "ODC-BY" in summary["source_attribution"]
    assert "finepdf-to-images/issues" in summary["takedown"]
    assert "not permission to redistribute" in summary["limitations"]


def test_the_summary_is_stable() -> None:
    assert policy_summary() == policy_summary()
