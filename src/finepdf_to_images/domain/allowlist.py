"""The curated licence allow list: the only route by which bytes may be published.

:mod:`finepdf_to_images.domain.policy` accepts exactly one basis for republishing an artifact's
bytes -- ``EvidenceSource.CURATED_ALLOWLIST``, "a human decision recorded in this repository".
This module *is* that record. Every entry was approved deliberately, and the note says on what
basis, so a reviewer can disagree with a specific claim rather than with a mood.

Two properties are deliberate and tested:

**Entries are keyed by exact host.** Not by suffix. ``nih.gov`` in this file would not clear
``evil.nih.gov.attacker.test``, because nothing here is matched as a substring or a suffix.

**Every host in a retrieval's redirect chain must be allow listed, and to the same entry.** A
request that starts at an allow-listed host and ends somewhere else has left the permission
behind; inheriting it would let any open redirect launder arbitrary bytes into the publication.

None of these documents states a licence in its own text. They are open by *statute* rather than
by declaration, which is precisely why the decision has to be curated and recorded rather than
detected: no scanner can read 17 U.S.C. 105 off a page that does not mention it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from urllib.parse import urlsplit

from finepdf_to_images.domain.policy import EvidenceSource, LicenseDeclaration, LicenseStatus


@dataclasses.dataclass(frozen=True, slots=True)
class AllowlistEntry:
    """One approved source, and the basis on which it was approved."""

    host: str
    identifier: str
    basis: str

    def declaration(self) -> LicenseDeclaration:
        return LicenseDeclaration(
            status=LicenseStatus.DECLARED_OPEN,
            identifier=self.identifier,
            evidence=EvidenceSource.CURATED_ALLOWLIST,
            note=f"{self.host}: {self.basis}",
        )


#: Approved sources, keyed by exact lowercase host.
#:
#: Each ``basis`` names the specific instrument that puts the work outside copyright or grants
#: reuse. "It looked official" is not a basis and must never appear here.
CURATED: Mapping[str, AllowlistEntry] = {
    entry.host: entry
    for entry in (
        AllowlistEntry(
            host="ntp.niehs.nih.gov",
            identifier="public-domain",
            basis=(
                "Work of the United States federal government (National Toxicology Program, "
                "NIEHS). No copyright subsists in it under 17 U.S.C. 105."
            ),
        ),
        AllowlistEntry(
            host="archive.opengazettes.org.za",
            identifier="public-domain",
            basis=(
                "South African provincial gazette. Section 12(8)(a) of the Copyright Act 98 of "
                "1978 denies copyright to official texts of a legislative, administrative or "
                "legal nature."
            ),
        ),
        AllowlistEntry(
            host="eeas.europa.eu",
            identifier="CC-BY-4.0",
            basis=(
                "European Union institutional document. Reuse is authorised under Commission "
                "Decision 2011/833/EU on equivalent attribution terms. Note the Decision does "
                "not extend to any third-party material such a document may quote."
            ),
        ),
    )
}


def host_of(url: str) -> str:
    """The lowercase host of ``url``, or ``""`` if it has none."""
    return (urlsplit(url).hostname or "").lower()


def entry_for(*urls: str | None) -> AllowlistEntry | None:
    """The approved entry covering **every** given URL, or ``None``.

    ``urls`` is a retrieval's whole chain: the requested URL and the final one after redirects.
    All of them must resolve to the same entry. A missing or empty URL is not a free pass -- it
    is an unknown host, and an unknown host is not allow listed.
    """
    if not urls:
        return None
    entries = {CURATED.get(host_of(url or "")) for url in urls}
    if len(entries) != 1:
        return None
    entry = entries.pop()
    return entry


def declaration_for(*urls: str | None) -> LicenseDeclaration:
    """The licence declaration for a retrieval chain.

    Returns the default ``UNKNOWN`` declaration when the chain is not covered, which the policy
    reduces to ``metadata-only``. There is no argument that changes that.
    """
    entry = entry_for(*urls)
    return entry.declaration() if entry is not None else LicenseDeclaration()


def allowlist_summary() -> list[dict[str, str]]:
    """The allow list, for the manifest and the dataset card. Sorted for a stable digest."""
    return [
        {"host": entry.host, "identifier": entry.identifier, "basis": entry.basis}
        for entry in sorted(CURATED.values(), key=lambda entry: entry.host)
    ]
