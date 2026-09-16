"""Provenance and the public-publication policy.

Pure and deliberately conservative.

The premise this module exists to defend: **a document being in FinePDFs does not grant anyone
permission to republish it.** FinePDFs itself is ODC-BY, which covers the dataset — the extracted
text, the metadata, the compilation. It says nothing about the copyright in the PDFs those rows
point at, which belong to whoever published them. An HTTP 200 is not a licence either; it means a
server sent bytes, not that the bytes may be redistributed.

So the default answer for a retrieved artifact is *no*. Publication of the bytes requires a
positive, recorded, allow-listed declaration. Everything else is reduced to a metadata and hash
reference, which is enough to reproduce and verify the run without redistributing the work.

Keeping this separate from scoring and extraction is the point: it is one testable decision
function, and the tests can prove that no input path turns "unknown" into "allowed".
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from typing import Any

#: Attribution required by the source dataset's own licence.
SOURCE_ATTRIBUTION = (
    "Source documents were identified through HuggingFaceFW/finepdfs "
    "(https://huggingface.co/datasets/HuggingFaceFW/finepdfs), licensed ODC-BY."
)

#: Where to report a problem with a published artifact. Deliberately a public issue tracker rather
#: than a personal address: a takedown route that depends on one inbox is not a takedown route.
TAKEDOWN_CONTACT = (
    "Open an issue at https://github.com/NoeFlandre/finepdf-to-images/issues, or use the "
    "Hugging Face dataset's Community tab, to request removal of any artifact. Requests are "
    "honoured without requiring the requester to prove ownership."
)

LIMITATION_STATEMENT = (
    "Source documents were published by third parties under terms this project does not control "
    "and cannot verify at scale. Inclusion of a row in FinePDFs is not permission to redistribute "
    "the document behind it. Artifacts whose redistribution status could not be established are "
    "represented by metadata and hashes only."
)


class LicenseStatus(enum.StrEnum):
    """What is actually known about an artifact's redistribution terms.

    ``UNKNOWN`` is the default and by far the most common real answer. It is a distinct value from
    ``ABSENT`` on purpose: "we did not establish any terms" and "the source states there are no
    terms" are different claims, and collapsing them is how an unknown quietly becomes a licence.
    """

    UNKNOWN = "unknown"
    ABSENT = "absent"
    DECLARED_OPEN = "declared-open"
    DECLARED_RESTRICTED = "declared-restricted"
    INCOMPATIBLE = "incompatible"


class EvidenceSource(enum.StrEnum):
    """How a licence claim reached us, and therefore how much it is worth.

    Only :attr:`CURATED_ALLOWLIST` — a human decision recorded in this repository — can support
    publishing bytes. A licence string scraped from a response header or guessed from a URL is
    evidence of what a server said, not of what a rights holder permits.
    """

    NONE = "none"
    URL_HEURISTIC = "url-heuristic"
    RESPONSE_HEADER = "response-header"
    CURATED_ALLOWLIST = "curated-allowlist"


#: Evidence sources that may justify redistributing bytes. Exactly one, and it requires a human.
TRUSTED_EVIDENCE: frozenset[EvidenceSource] = frozenset({EvidenceSource.CURATED_ALLOWLIST})

#: Licence identifiers this project is willing to redistribute under. Short on purpose: every entry
#: is a commitment, and a longer list is not a better one.
ALLOWED_LICENSES: frozenset[str] = frozenset(
    {
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "PDM-1.0",
        "public-domain",
    }
)

#: Provenance fields without which a published row cannot be traced back to its FinePDFs row and
#: source URL. Issue #1 requires exactly this traceability, so an incomplete row is not published
#: in any form.
REQUIRED_PROVENANCE: tuple[str, ...] = (
    "dataset",
    "revision",
    "config",
    "split",
    "shard",
    "row_index",
    "row_id",
    "url",
)


class Disposition(enum.StrEnum):
    """What may be published for an artifact."""

    PUBLISH_ARTIFACT = "publish-artifact"
    """Provenance, hashes, and the artifact bytes."""

    METADATA_ONLY = "metadata-only"
    """Provenance and hashes. No bytes. The default for anything not positively cleared."""

    EXCLUDE = "exclude"
    """Nothing at all: the row cannot be traced, so publishing it would assert what we
    cannot show."""


@dataclasses.dataclass(frozen=True, slots=True)
class LicenseDeclaration:
    """What is known about one artifact's terms, and how we came to know it."""

    status: LicenseStatus = LicenseStatus.UNKNOWN
    identifier: str | None = None
    evidence: EvidenceSource = EvidenceSource.NONE
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "identifier": self.identifier,
            "evidence": str(self.evidence),
            "note": self.note,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PublicationDecision:
    """The policy's answer, with the reason attached so a reviewer can argue with it."""

    disposition: Disposition
    reason: str
    license: LicenseDeclaration

    @property
    def publishes_bytes(self) -> bool:
        return self.disposition is Disposition.PUBLISH_ARTIFACT

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": str(self.disposition),
            "reason": self.reason,
            "license": self.license.as_dict(),
        }


def missing_provenance(provenance: Mapping[str, Any]) -> list[str]:
    """Required provenance fields that are absent or empty.

    ``row_index`` of ``0`` is a legitimate value, so emptiness is tested by ``is None`` and blank
    strings rather than by falsiness.
    """
    missing = []
    for field in REQUIRED_PROVENANCE:
        value = provenance.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def decide(
    provenance: Mapping[str, Any],
    license: LicenseDeclaration | None = None,
    allowed_licenses: Sequence[str] | frozenset[str] = ALLOWED_LICENSES,
) -> PublicationDecision:
    """Decide what may be published for one artifact.

    The order of the checks is the policy:

    1. Without complete provenance the row cannot be traced back to FinePDFs and its source URL,
       so it is excluded outright rather than published as an untraceable metadata row.
    2. Bytes are published only for a ``DECLARED_OPEN`` status, carrying an allow-listed
       identifier, backed by trusted evidence. All three, together.
    3. Everything else is metadata and hashes only.

    There is no branch that promotes an unknown to an allowed licence, and no default argument that
    can be set to make one appear. That absence is the whole design, and it is what the tests
    assert.
    """
    declaration = license or LicenseDeclaration()

    absent = missing_provenance(provenance)
    if absent:
        return PublicationDecision(
            disposition=Disposition.EXCLUDE,
            reason=f"incomplete provenance: missing {', '.join(absent)}",
            license=declaration,
        )

    if declaration.status is not LicenseStatus.DECLARED_OPEN:
        return PublicationDecision(
            disposition=Disposition.METADATA_ONLY,
            reason=(
                f"redistribution not established (status {declaration.status}); "
                "publishing provenance and hashes only"
            ),
            license=declaration,
        )

    if declaration.evidence not in TRUSTED_EVIDENCE:
        return PublicationDecision(
            disposition=Disposition.METADATA_ONLY,
            reason=(
                f"licence claim rests on {declaration.evidence}, which is not trusted evidence "
                "for redistribution; publishing provenance and hashes only"
            ),
            license=declaration,
        )

    if declaration.identifier not in set(allowed_licenses):
        return PublicationDecision(
            disposition=Disposition.METADATA_ONLY,
            reason=(
                f"licence {declaration.identifier!r} is not on this project's allow list; "
                "publishing provenance and hashes only"
            ),
            license=declaration,
        )

    return PublicationDecision(
        disposition=Disposition.PUBLISH_ARTIFACT,
        reason=f"{declaration.identifier} confirmed by {declaration.evidence}",
        license=declaration,
    )


def policy_summary() -> dict[str, Any]:
    """The policy in machine-readable form, for the dataset card and the run manifest."""
    return {
        "default_disposition": str(Disposition.METADATA_ONLY),
        "allowed_licenses": sorted(ALLOWED_LICENSES),
        "trusted_evidence": sorted(str(source) for source in TRUSTED_EVIDENCE),
        "required_provenance": list(REQUIRED_PROVENANCE),
        "source_attribution": SOURCE_ATTRIBUTION,
        "limitations": LIMITATION_STATEMENT,
        "takedown": TAKEDOWN_CONTACT,
    }
