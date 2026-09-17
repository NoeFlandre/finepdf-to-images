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
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from typing import Any

#: Attribution required by the source dataset's own licence.
SOURCE_ATTRIBUTION = (
    "Source documents were identified through HuggingFaceFW/finepdfs "
    "(https://huggingface.co/datasets/HuggingFaceFW/finepdfs), licensed ODC-BY. "
    "Extracted document text is republished under ODC-BY; downstream distribution "
    "or reuse must attribute HuggingFaceFW/finepdfs."
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

#: Additionally required once a decision concerns retrieved bytes. ``metadata-only`` is only a
#: meaningful fallback if the metadata identifies *which* bytes it stands for, so a row about an
#: artifact must carry that artifact's digest. The retrieval and extraction stages pass
#: ``require_artifact_hash=True``.
ARTIFACT_PROVENANCE: tuple[str, ...] = (*REQUIRED_PROVENANCE, "sha256")

#: Fields that must be whole numbers rather than text.
_INTEGER_FIELDS: frozenset[str] = frozenset({"row_index"})

#: Fields that must be a SHA-256 digest. Presence alone is not enough: the point of requiring a
#: digest is that the metadata identifies *which* bytes it stands for, and "not-a-hash" identifies
#: nothing.
_DIGEST_FIELDS: frozenset[str] = frozenset({"sha256"})
_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")


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

    def __post_init__(self) -> None:
        # StrEnum members compare equal to their string values, so a raw "curated-allowlist"
        # would otherwise slip through the evidence gate while a raw status string correctly
        # failed. Asymmetric trust like that is how a policy rots; both are checked here.
        if not isinstance(self.status, LicenseStatus):
            raise TypeError(f"status must be a LicenseStatus, got {self.status!r}")
        if not isinstance(self.evidence, EvidenceSource):
            raise TypeError(f"evidence must be an EvidenceSource, got {self.evidence!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LicenseDeclaration:
        """Rebuild from :meth:`as_dict`.

        ``__post_init__`` refuses raw strings, so a serializer without a matching deserializer
        would push the enum coercion into every caller.
        """
        return cls(
            status=LicenseStatus(data["status"]),
            identifier=data.get("identifier"),
            evidence=EvidenceSource(data["evidence"]),
            note=data.get("note", ""),
        )

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


def missing_provenance(
    provenance: Mapping[str, Any], required: tuple[str, ...] = REQUIRED_PROVENANCE
) -> list[str]:
    """Required provenance fields that are absent, empty, or of the wrong type.

    A falsiness test would drop ``row_index`` of ``0``, which is the shard's first row and a
    perfectly valid value. An ``is None`` plus blank-string test, which is what this was, goes too
    far the other way: ``url=False``, ``url=0`` and ``url=[]`` all passed as present, so a row with
    no usable url could be published as "traceable". Each field is therefore checked against the
    type it is actually supposed to be.
    """
    missing = []
    for field in required:
        if not _is_usable(field, provenance.get(field)):
            missing.append(field)
    return missing


def _is_usable(field: str, value: Any) -> bool:
    if field in _INTEGER_FIELDS:
        return _is_whole_number(value)
    if not _is_clean_text(value):
        return False
    return bool(_DIGEST_RE.fullmatch(value)) if field in _DIGEST_FIELDS else True


def _is_whole_number(value: Any) -> bool:
    """bool is an int subclass; ``True`` as a row index is a bug, not a row."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_clean_text(value: Any) -> bool:
    """Non-empty text with no surrounding whitespace.

    Padding is rejected rather than trimmed: the allow list already refuses " CC-BY-4.0 ", so
    accepting it here would make the module inconsistent about whether padding matters.
    """
    return isinstance(value, str) and bool(value) and value == value.strip()


def decide(
    provenance: Mapping[str, Any],
    license: LicenseDeclaration | None = None,
    allowed_licenses: AbstractSet[str] = ALLOWED_LICENSES,
    *,
    require_artifact_hash: bool = False,
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
    # A bare str is a valid Sequence[str], so an earlier signature let
    # allowed_licenses="CC-BY-4.0" turn the allow list into a set of single characters, and an
    # identifier of "C" published. Only a real set is accepted now.
    if not isinstance(allowed_licenses, AbstractSet):
        raise TypeError(
            f"allowed_licenses must be a set of identifiers, got {type(allowed_licenses).__name__}"
        )

    absent = missing_provenance(
        provenance, ARTIFACT_PROVENANCE if require_artifact_hash else REQUIRED_PROVENANCE
    )
    if absent:
        return PublicationDecision(
            disposition=Disposition.EXCLUDE,
            reason=f"incomplete provenance: missing {', '.join(absent)}",
            license=declaration,
        )

    refusal = _refusal_reason(declaration, allowed_licenses)
    if refusal:
        return PublicationDecision(
            disposition=Disposition.METADATA_ONLY, reason=refusal, license=declaration
        )
    return PublicationDecision(
        disposition=Disposition.PUBLISH_ARTIFACT,
        reason=f"{declaration.identifier} confirmed by {declaration.evidence}",
        license=declaration,
    )


def _refusal_reason(declaration: LicenseDeclaration, allowed_licenses: AbstractSet[str]) -> str:
    """Why these bytes may not be republished, or an empty string if they may.

    All three conditions, together. There is no ordering here that lets one compensate for
    another, which is the property the tests assert over the whole cross-product.
    """
    if declaration.status is not LicenseStatus.DECLARED_OPEN:
        return (
            f"redistribution not established (status {declaration.status}); "
            "publishing provenance and hashes only"
        )
    if declaration.evidence not in TRUSTED_EVIDENCE:
        return (
            f"licence claim rests on {declaration.evidence}, which is not trusted evidence "
            "for redistribution; publishing provenance and hashes only"
        )
    if declaration.identifier not in allowed_licenses:
        return (
            f"licence {declaration.identifier!r} is not on this project's allow list; "
            "publishing provenance and hashes only"
        )
    return ""


def policy_summary() -> dict[str, Any]:
    """The policy in machine-readable form, for the dataset card and the run manifest."""
    return {
        "default_disposition": str(Disposition.METADATA_ONLY),
        "allowed_licenses": sorted(ALLOWED_LICENSES),
        "trusted_evidence": sorted(str(source) for source in TRUSTED_EVIDENCE),
        "required_provenance": list(REQUIRED_PROVENANCE),
        "required_artifact_provenance": list(ARTIFACT_PROVENANCE),
        "source_attribution": SOURCE_ATTRIBUTION,
        "limitations": LIMITATION_STATEMENT,
        "takedown": TAKEDOWN_CONTACT,
    }
