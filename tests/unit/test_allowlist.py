"""The curated allow list.

This is the only module in the project that can turn "unknown" into "may publish the bytes", so
the tests here are about what it *refuses*. A bug that makes it too permissive republishes someone
else's work; a bug that makes it too strict publishes less than it could, which is recoverable.
"""

from __future__ import annotations

import pytest

from finepdf_to_images.domain.allowlist import (
    CURATED,
    AllowlistEntry,
    allowlist_summary,
    declaration_for,
    entry_for,
    host_of,
)
from finepdf_to_images.domain.policy import (
    ALLOWED_LICENSES,
    Disposition,
    EvidenceSource,
    LicenseStatus,
    decide,
)

APPROVED = "ntp.niehs.nih.gov"
APPROVED_URL = f"https://{APPROVED}/a.pdf"
PROVENANCE = {
    "dataset": "d",
    "revision": "r",
    "config": "c",
    "split": "s",
    "shard": "sh",
    "row_index": 0,
    "row_id": "row-1",
    "url": APPROVED_URL,
    "sha256": "a" * 64,
}


# --------------------------------------------------------------------------- the entries


def test_every_entry_carries_an_allowed_identifier() -> None:
    """An entry naming a licence the project does not redistribute under would be inert."""
    for entry in CURATED.values():
        assert entry.identifier in ALLOWED_LICENSES, entry.host


def test_every_entry_states_a_specific_legal_basis() -> None:
    """The note is the audit trail. "Looks official" is not a basis, so require a real citation."""
    for entry in CURATED.values():
        assert len(entry.basis) > 60, f"{entry.host}: basis is too thin to review"
        assert any(
            token in entry.basis
            for token in ("U.S.C.", "Copyright Act", "Decision 2011/833/EU", "Directive")
        ), f"{entry.host}: basis cites no instrument"


def test_entries_are_keyed_by_their_own_host() -> None:
    for host, entry in CURATED.items():
        assert host == entry.host == host.lower()


def test_an_entry_declares_open_status_backed_by_the_curated_allowlist() -> None:
    declaration = CURATED[APPROVED].declaration()
    assert declaration.status is LicenseStatus.DECLARED_OPEN
    assert declaration.evidence is EvidenceSource.CURATED_ALLOWLIST
    assert APPROVED in declaration.note


# --------------------------------------------------------------------------- matching


def test_an_approved_host_is_matched() -> None:
    assert entry_for(f"https://{APPROVED}/some/file.pdf") is CURATED[APPROVED]


def test_matching_ignores_case_and_port() -> None:
    assert entry_for(f"https://{APPROVED.upper()}:443/a.pdf") is CURATED[APPROVED]


def test_an_unknown_host_is_not_matched() -> None:
    assert entry_for("https://example.org/a.pdf") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/ntp.niehs.nih.gov/a.pdf",
        f"https://{APPROVED}.attacker.test/a.pdf",
        f"https://not-{APPROVED}/a.pdf",
        f"https://attacker.test/?u=https://{APPROVED}/a.pdf",
        f"https://attacker.test#{APPROVED}",
        f"https://user:pw@attacker.test/{APPROVED}",
    ],
)
def test_a_host_is_never_matched_as_a_substring_or_suffix(url: str) -> None:
    """The approved host appearing *somewhere* in a URL is not the approved host."""
    assert entry_for(url) is None


def test_a_parent_domain_does_not_clear_a_subdomain_or_the_reverse() -> None:
    assert entry_for("https://nih.gov/a.pdf") is None
    assert entry_for(f"https://sub.{APPROVED}/a.pdf") is None


# --------------------------------------------------------------------------- redirect chains


def test_a_chain_that_stays_on_one_approved_host_is_matched() -> None:
    assert entry_for(f"https://{APPROVED}/a.pdf", f"https://{APPROVED}/b.pdf") is CURATED[APPROVED]


def test_a_chain_that_leaves_the_approved_host_is_refused() -> None:
    """An open redirect on an approved host must not launder arbitrary bytes into the dataset."""
    assert entry_for(f"https://{APPROVED}/a.pdf", "https://attacker.test/a.pdf") is None


def test_a_chain_that_arrives_at_an_approved_host_from_elsewhere_is_refused() -> None:
    assert entry_for("https://example.org/a.pdf", f"https://{APPROVED}/a.pdf") is None


def test_a_chain_spanning_two_different_approved_hosts_is_refused() -> None:
    """Both are approved, but the pair is a redirect between sources, which nobody approved."""
    hosts = sorted(CURATED)
    assert entry_for(f"https://{hosts[0]}/a.pdf", f"https://{hosts[1]}/a.pdf") is None


@pytest.mark.parametrize("missing", [None, "", "not a url", "://", "file:///etc/passwd"])
def test_an_unusable_url_in_the_chain_refuses_the_whole_chain(missing: str | None) -> None:
    assert entry_for(f"https://{APPROVED}/a.pdf", missing) is None


def test_no_urls_at_all_is_refused() -> None:
    assert entry_for() is None


# --------------------------------------------------------------------------- the policy join


def test_an_approved_chain_reaches_publish_artifact() -> None:
    decision = decide(PROVENANCE, declaration_for(APPROVED_URL), require_artifact_hash=True)
    assert decision.disposition is Disposition.PUBLISH_ARTIFACT
    assert decision.publishes_bytes


def test_an_unapproved_chain_stays_metadata_only() -> None:
    decision = decide(
        PROVENANCE, declaration_for("https://example.org/a.pdf"), require_artifact_hash=True
    )
    assert decision.disposition is Disposition.METADATA_ONLY
    assert not decision.publishes_bytes


def test_an_approved_host_with_incomplete_provenance_is_still_excluded() -> None:
    """The allow list clears the licence question only. It does not excuse an untraceable row."""
    provenance = {key: value for key, value in PROVENANCE.items() if key != "row_id"}
    decision = decide(provenance, declaration_for(APPROVED_URL), require_artifact_hash=True)
    assert decision.disposition is Disposition.EXCLUDE


def test_declaration_for_an_unapproved_chain_is_the_unknown_default() -> None:
    declaration = declaration_for("https://example.org/a.pdf")
    assert declaration.status is LicenseStatus.UNKNOWN
    assert declaration.evidence is EvidenceSource.NONE
    assert declaration.identifier is None


# --------------------------------------------------------------------------- reporting


def test_the_summary_is_sorted_and_complete() -> None:
    summary = allowlist_summary()
    assert [row["host"] for row in summary] == sorted(CURATED)
    assert all(row["basis"] and row["identifier"] for row in summary)


def test_host_of_is_empty_for_a_url_without_one() -> None:
    assert host_of("not a url") == ""


def test_an_entry_is_immutable() -> None:
    """The allow list is a record of decisions, so nothing may edit one at runtime."""
    with pytest.raises(AttributeError):
        CURATED[APPROVED].host = "attacker.test"  # ty: ignore[invalid-assignment]


def test_an_entry_can_be_constructed_only_with_all_three_fields() -> None:
    with pytest.raises(TypeError):
        AllowlistEntry(host="a.test", identifier="public-domain")  # ty: ignore[missing-argument]
