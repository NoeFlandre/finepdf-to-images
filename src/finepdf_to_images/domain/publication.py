"""The published dataset: its schema, its card, and what a publication would upload.

Pure. Assembling the rows, rendering the card and deciding which files a publication consists of
all happen here over plain values; talking to the Hub is the adapter's job. That split is what
makes "dry-run does not mutate the Hub" a structural fact rather than a promise -- the dry run
simply never reaches the adapter's write path.

The card is **generated**, not written. Its policy and vocabulary sections come from
:func:`policy_summary` and :func:`vocabulary_summary`, so the published description of the rules
cannot drift from the code that enforces them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.allowlist import allowlist_summary
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import canonical_bytes, content_digest, sha256_hex

#: Bumped when the published row shape changes. Consumers index on these names.
SCHEMA_VERSION = 2

DEFAULT_REPO = "NoeFlandre/finepdf-to-images-poc"

#: Hard ceiling on total published text bytes across all documents.
#: The 1000-row pilot yields ~24 MB; 50 MB prevents unbounded text payload dumps.
MAX_DOCUMENT_TEXT_BYTES = 50 * 1024 * 1024

#: Hard ceiling on total published artifact bytes -- images and PDFs together.
#:
#: Unlike the text cap this bounds *third-party works*, so it is deliberately small. The pilot's
#: three allow-listed sources come to under 2 MB; 64 MB leaves room to grow without any chance of
#: a mistake in the allow list quietly turning into a multi-gigabyte redistribution.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

#: ``namespace/name`` as the Hub spells it. The destination is interpolated into API calls and
#: printed in the card, so it is validated rather than trusted -- the same rule SourceRef applies
#: to where the data comes *from*.
#: A SHA-256 hex digest. Used to refuse artifact paths that identify nothing.
_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")

_REPO_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,94}/[A-Za-z0-9][A-Za-z0-9._-]{0,94}\Z")

#: Files a publication always writes, in upload order.
DOCUMENTS_FILE = "data/documents.jsonl"
DOCUMENTS_RELEVANT_FILE = "data/documents_relevant.jsonl"
DOCUMENTS_RETRIEVED_FILE = "data/documents_retrieved.jsonl"
IMAGES_FILE = "data/images.jsonl"
MANIFEST_FILE = "manifest.json"
CARD_FILE = "README.md"

#: The published dataset: one parquet, one row per relevant document.
#:
#: The six-file JSONL layout this replaces was shaped by the pipeline's stages rather than by a
#: reader: 203 files, 18 columns of bookkeeping, and 948 of 1000 rows that a reader has no use
#: for. Images were loose files addressed by digest, invisible in the viewer and reachable only by
#: joining two JSONL files by hand.
DATASET_FILE = "data/train-00000-of-00001.parquet"

#: The published columns, as (field, description). Emitted into the card, so the documented
#: schema is generated from the same tuple the rows are built from and cannot drift from it.
DATASET_FIELDS: tuple[tuple[str, str], ...] = (
    ("pdf_url", "the source PDF this row was built from"),
    ("text", "text extracted from that PDF"),
    ("images", "the images embedded in that PDF, rendered inline by the viewer"),
    ("matched_terms", "the vocabulary terms that made this row relevant"),
)

#: The published row schema, as (field, description). Emitted into the card so the documentation
#: and the data are generated from one source.
DOCUMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("row_index", "position of the row in the source shard"),
    ("row_id", "FinePDFs row id, e.g. `<urn:uuid:...>`"),
    ("url", "source document URL as recorded by FinePDFs"),
    ("final_url", "URL the bytes were actually served from, after any redirect"),
    ("language", "language recorded by FinePDFs for the row"),
    ("text", "extracted document text from FinePDFs (ODC-BY)"),
    ("text_sha256", "SHA-256 of the extracted text UTF-8 bytes"),
    ("relevant", "whether the agriculture scorer marked the row relevant"),
    ("relevance_score", "number of distinct concept groups matched"),
    ("matched_terms", "the exact vocabulary terms that produced the decision"),
    ("retrieved", "whether the source PDF was successfully retrieved"),
    ("failure_reason", "why retrieval produced no artifact, when it did not"),
    ("pdf_sha256", "SHA-256 of the retrieved PDF bytes"),
    ("pdf_bytes", "size of the retrieved PDF in bytes"),
    ("image_count", "number of images extracted from the PDF"),
    ("pdf", "path to the published source PDF, for allow-listed sources; null otherwise"),
    ("disposition", "what the publication policy permits for this artifact"),
    ("license_status", "what is known about the document's redistribution terms"),
)

IMAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("row_id", "FinePDFs row id of the document the image came from"),
    ("pdf_sha256", "SHA-256 of the PDF the image was extracted from"),
    ("page_index", "zero-based page the image appears on"),
    ("image_index", "zero-based position of the image on that page"),
    ("sha256", "SHA-256 of the extracted image bytes"),
    ("mime", "media type of the extracted image"),
    ("width", "decoded width in pixels"),
    ("height", "decoded height in pixels"),
    ("byte_size", "size of the extracted image in bytes"),
    ("duplicate_of", "reference to the first occurrence, when the bytes repeat"),
    ("image", "the image itself, for allow-listed sources; null when only metadata is published"),
)


class PublicationError(ValueError):
    """Inputs that cannot be published as they stand."""


@dataclasses.dataclass(frozen=True, slots=True)
class PublishFile:
    """One file a publication would write, with the identity it will have."""

    path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)

    @property
    def git_blob_sha1(self) -> str:
        """The git object id this content would have.

        The Hub exposes a content SHA-256 only for LFS objects. Every file this stage publishes is
        small enough to be an ordinary git blob, so the SHA-256 alone could never match anything
        the Hub reports -- verification would fail on every successful publication and a re-run
        would never be recognised as a no-op. Git's own object id is the identity that *is*
        available, and it is just as much a content hash.
        """
        header = f"blob {len(self.data)}\0".encode()
        return hashlib.sha1(header + self.data, usedforsecurity=False).hexdigest()

    @property
    def size(self) -> int:
        return len(self.data)

    def matches(self, remote_digest: str | None) -> bool:
        """Whether a digest the Hub reported is this content, by either identity it may use."""
        return remote_digest is not None and remote_digest in {self.sha256, self.git_blob_sha1}

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclasses.dataclass(frozen=True, slots=True)
class PublicationPlan:
    """Everything a publication would do, computed without touching the Hub."""

    repo: str
    files: tuple[PublishFile, ...]
    manifest: Mapping[str, Any]

    @property
    def digest(self) -> str:
        """One identity for the whole publication, over every file's path and content."""
        return content_digest([file.as_dict() for file in self.files])

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "digest": self.digest,
            "files": [file.as_dict() for file in self.files],
            "counts": dict(self.manifest["counts"]),
        }


def _index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, Mapping[str, Any]]:
    return {row.get(key): row for row in rows}


def build_document_rows(
    *,
    scored: Sequence[Mapping[str, Any]],
    retrieved: Sequence[Mapping[str, Any]],
    extracted: Sequence[Mapping[str, Any]],
    max_text_bytes: int = MAX_DOCUMENT_TEXT_BYTES,
) -> list[dict[str, Any]]:
    """One published row per scored document, in shard order.

    Every scored row appears, not only the retrieved ones. A row that was judged irrelevant, or
    whose URL was refused, or whose server returned HTML, is part of the result: a dataset that
    silently drops its failures cannot be used to reproduce the run or to argue with the scorer.
    """
    by_retrieval = _index(retrieved, "row_id")
    by_extraction = _index(extracted, "row_id")

    rows = [
        _document_row(
            row,
            by_retrieval.get(row.get("row_id"), {}),
            by_extraction.get(row.get("row_id"), {}),
        )
        for row in scored
    ]
    total_text_bytes = sum(len(row["text"].encode("utf-8")) for row in rows)
    if total_text_bytes > max_text_bytes:
        raise PublicationError(
            f"total published text bytes {total_text_bytes} exceeds cap of {max_text_bytes} bytes"
        )
    return sorted(rows, key=lambda row: (row["row_index"] is None, row["row_index"]))


def _document_row(
    scored: Mapping[str, Any], retrieval: Mapping[str, Any], extraction: Mapping[str, Any]
) -> dict[str, Any]:
    """One published row, joining what each stage knows about the same document."""
    relevance = _section(scored, "relevance")
    publication = _section(retrieval, "publication")
    licence = _section(publication, "license")
    text = _text(scored, "text")
    actual_sha256 = sha256_hex(text.encode("utf-8"))
    recorded_sha256 = scored.get("text_sha256")
    if recorded_sha256 is not None and recorded_sha256 != actual_sha256:
        raise PublicationError(
            f"text_sha256 mismatch for row {scored.get('row_id')!r}: "
            f"recorded {recorded_sha256!r} != computed {actual_sha256!r}"
        )
    return {
        "row_index": scored.get("row_index"),
        "row_id": scored.get("row_id"),
        "url": scored.get("url"),
        "final_url": _text(retrieval, "final_url"),
        "language": _text(relevance, "language"),
        "text": text,
        "text_sha256": actual_sha256,
        "relevant": bool(relevance.get("relevant")),
        "relevance_score": _number(relevance, "score"),
        "matched_terms": list(relevance.get("matched_terms") or ()),
        "retrieved": bool(retrieval.get("ok")),
        "failure_reason": _text(retrieval, "reason"),
        "pdf_sha256": _text(retrieval, "sha256"),
        "pdf_bytes": _number(retrieval, "byte_size"),
        "image_count": _number(extraction, "image_count"),
        "pdf": _published_pdf_path(publication, _text(retrieval, "sha256")),
        "disposition": _text(publication, "disposition"),
        "license_status": _text(licence, "status"),
    }


def _published_pdf_path(publication: Mapping[str, Any], digest: str) -> str | None:
    """Where this row's PDF is published, or ``None`` when only its metadata is.

    Derived from the disposition the policy already recorded rather than taken as an argument:
    the column then cannot disagree with the decision it reports, and a row whose bytes were never
    cleared has no path to point at.
    """
    if publication.get("disposition") != str(policy.Disposition.PUBLISH_ARTIFACT):
        return None
    return artifact_path(digest) if _DIGEST_RE.match(digest) else None


def _section(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested block, defaulted to empty. Stage outputs omit what does not apply."""
    value = row.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value) if isinstance(value, str) else ""


def _number(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def derive_relevant_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents judged relevant by the scorer, in shard order."""
    return [dict(row) for row in documents if bool(row.get("relevant"))]


def derive_retrieved_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents whose source PDF was successfully retrieved, in shard order."""
    return [dict(row) for row in documents if bool(row.get("retrieved"))]


def cleared_row_ids(documents: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Row ids the policy cleared for byte publication.

    This is the only thing that decides whether an artifact may ship. It reads the disposition the
    policy already wrote; it does not re-derive it, because a second implementation of the rule is
    a second thing that can disagree with it.
    """
    return frozenset(
        str(row.get("row_id"))
        for row in documents
        if row.get("disposition") == str(policy.Disposition.PUBLISH_ARTIFACT)
    )


def cleared_pdf_digests(documents: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Digest -> row id, for the PDFs of cleared rows that actually have one."""
    return {
        str(row["pdf_sha256"]): str(row.get("row_id"))
        for row in documents
        if row.get("disposition") == str(policy.Disposition.PUBLISH_ARTIFACT)
        and _DIGEST_RE.match(str(row.get("pdf_sha256") or ""))
    }


def cleared_image_digests(
    images: Sequence[Mapping[str, Any]], cleared: frozenset[str]
) -> dict[str, str]:
    """Digest -> mime, for images belonging to cleared rows.

    Takes the **raw** extraction index, which keys its owner as ``document_row_id``; the published
    rows rename that to ``row_id``. Passing the wrong one silently yields nothing, so the two
    shapes are never conflated: this is the only function that reads the raw key.

    Keyed by digest because the same image can appear on several pages and in several rows; it is
    published once, and every row referencing it points at that one path.
    """
    shipped: dict[str, str] = {}
    for image in images:
        digest = str(image.get("sha256") or "")
        if str(image.get("document_row_id")) in cleared and _DIGEST_RE.match(digest):
            shipped[digest] = str(image.get("mime") or "")
    return shipped


def build_image_rows(
    images: Sequence[Mapping[str, Any]], published: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """The image index, reduced to the published fields and stably ordered.

    ``published`` maps the digests whose bytes ship to their media type. A row for a digest that
    is not in it gets ``image: None`` rather than being dropped: the dataset should say what it
    declined to publish, not hide it.
    """
    shipped = dict(published or {})
    rows = [
        {
            "row_id": image.get("document_row_id"),
            "pdf_sha256": image.get("pdf_sha256"),
            "page_index": image.get("page_index"),
            "image_index": image.get("image_index"),
            "sha256": image.get("sha256"),
            "mime": image.get("mime"),
            "width": image.get("width"),
            "height": image.get("height"),
            "byte_size": image.get("byte_size"),
            "duplicate_of": image.get("duplicate_of"),
            "image": (
                image_path(str(image.get("sha256")), shipped[str(image.get("sha256"))])
                if str(image.get("sha256")) in shipped
                else None
            ),
        }
        for image in images
    ]
    return sorted(
        rows, key=lambda row: (str(row["pdf_sha256"]), row["page_index"], row["image_index"])
    )


def build_manifest(
    *,
    source: Mapping[str, Any],
    sampling: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    vocabulary_version: int,
    encoder: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The published run manifest.

    Carries no timestamp: two publications of the same pilot output must produce the same bytes,
    or the idempotency claim is decided by the clock rather than by the data.
    """
    relevant_docs = derive_relevant_rows(documents)
    retrieved_docs = derive_retrieved_rows(documents)
    doc_digest = content_digest(list(documents))
    relevant_digest = content_digest(relevant_docs)
    retrieved_digest = content_digest(retrieved_docs)
    img_digest = content_digest(list(images))
    return {
        "schema_version": SCHEMA_VERSION,
        "source": dict(source),
        "sampling": dict(sampling),
        "vocabulary_version": vocabulary_version,
        "encoder": dict(encoder or {}),
        "counts": _counts(documents, images),
        "splits": {
            "documents": {
                "all": {
                    "path": DOCUMENTS_FILE,
                    "count": len(documents),
                    "digest": doc_digest,
                },
                "relevant": {
                    "path": DOCUMENTS_RELEVANT_FILE,
                    "count": len(relevant_docs),
                    "digest": relevant_digest,
                },
                "retrieved": {
                    "path": DOCUMENTS_RETRIEVED_FILE,
                    "count": len(retrieved_docs),
                    "digest": retrieved_digest,
                },
            },
            "images": {
                "train": {
                    "path": IMAGES_FILE,
                    "count": len(images),
                    "digest": img_digest,
                },
            },
        },
        "publishes_source_bytes": any(
            row.get("disposition") == "publish-artifact" for row in documents
        ),
        "policy": policy.policy_summary(),
        "allowlist": allowlist_summary(),
        "documents_digest": doc_digest,
        "documents_relevant_digest": relevant_digest,
        "documents_retrieved_digest": retrieved_digest,
        "images_digest": img_digest,
    }


def _counts(
    documents: Sequence[Mapping[str, Any]], images: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    return {
        "documents": len(documents),
        "relevant": sum(1 for row in documents if row["relevant"]),
        "retrieved": sum(1 for row in documents if row["retrieved"]),
        "images": len(images),
        "unique_images": len({row["sha256"] for row in images}),
        "published_images": len(_published_paths(images, "image")),
        "published_pdfs": len(_published_paths(documents, "pdf")),
    }


def _published_paths(rows: Sequence[Mapping[str, Any]], field: str) -> set[str]:
    """The distinct artifact paths the rows point at. Empty when nothing is republished."""
    return {str(row[field]) for row in rows if row.get(field)}


def _validate_repo(repo: str) -> None:
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        raise PublicationError(f"invalid destination repo {repo!r}: expected namespace/name")


def _expected_artifact_paths(
    documents: Sequence[Mapping[str, Any]], images: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Digest -> the one path at which that artifact may be published.

    Derived from the *published rows* rather than recomputed from the raw extraction: the rows are
    what a consumer reads, so tying the payload to them makes "the index says an image is here"
    and "the bytes are here" the same statement. Recomputing let the two disagree -- and did: the
    published rows key by ``row_id`` while the raw index keys by ``document_row_id``, so a
    recomputed clearance silently matched nothing and published no images at all.
    """
    cleared = cleared_row_ids(documents)
    expected = {
        str(row["sha256"]): str(row["image"])
        for row in images
        # Both conditions, and the second is the one that matters: the policy's decision about
        # the owning document, not the caller's assertion that this image ships. Checking only
        # the index made the check no stricter than its caller for images -- the pipeline joined
        # correctly, so nothing was exploitable, but the interlock this replaced was removed on
        # the promise that these checks are stricter than the caller. For images they were not.
        if row.get("image") and str(row.get("row_id")) in cleared
    }
    expected.update({digest: artifact_path(digest) for digest in cleared_pdf_digests(documents)})
    return expected


def _check_one_artifact(artifact: PublishFile, expected: Mapping[str, str]) -> None:
    """Refuse one artifact that the policy did not clear, or that is not what its path says."""
    if not (artifact.path.startswith("pdfs/") or artifact.path.startswith("images/")):
        raise PublicationError(f"artifact path is neither a PDF nor an image: {artifact.path!r}")
    # Keyed by the digest of the bytes actually passed, so swapping the bytes under a cleared
    # path does not inherit that path's clearance.
    permitted = expected.get(artifact.sha256)
    if permitted is None:
        raise PublicationError(
            f"artifact {artifact.path!r} belongs to no row cleared for byte publication. "
            "Only the curated allow list can clear one."
        )
    if artifact.path != permitted:
        raise PublicationError(
            f"artifact path {artifact.path!r} does not match its own bytes (expected {permitted!r})"
        )


def _check_payload_as_a_whole(
    artifacts: Sequence[PublishFile], expected: Mapping[str, str]
) -> None:
    """The properties of the payload taken together, once each artifact is individually sound."""
    paths = [artifact.path for artifact in artifacts]
    if len(set(paths)) != len(paths):
        raise PublicationError(
            f"{len(paths) - len(set(paths))} artifact(s) are repeated. The same file twice would "
            "count twice against the cap and be uploaded twice."
        )

    total = sum(len(artifact.data) for artifact in artifacts)
    if total > MAX_ARTIFACT_BYTES:
        raise PublicationError(
            f"total published artifact bytes {total} exceeds cap of {MAX_ARTIFACT_BYTES} bytes"
        )

    # Last, because a specific complaint about a bad artifact is more useful than a general one
    # about a missing file, and a bad artifact usually explains the missing one.
    missing = sorted(set(expected.values()) - set(paths))
    if missing:
        raise PublicationError(
            f"{len(missing)} published row(s) point at artifact bytes that the plan does not "
            f"carry (first: {missing[0]!r}). A row pointing at a file nobody uploaded is a "
            "broken reference in the published dataset."
        )


def _check_artifacts(
    artifacts: Sequence[PublishFile],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
) -> None:
    """Refuse any artifact the policy did not clear, and any row whose bytes are missing.

    This replaced a blanket interlock that refused *all* byte publication. The interlock was the
    right default while nothing implemented upload; removing it is only safe because these checks
    replace it, so they are deliberately stricter than "the caller said so".
    """
    expected = _expected_artifact_paths(documents, images)
    for artifact in artifacts:
        _check_one_artifact(artifact, expected)

    _check_payload_as_a_whole(artifacts, expected)


def _check_text_byte_cap(*published: Sequence[Mapping[str, Any]]) -> None:
    """Bound the text bytes the plan actually publishes, counting every file it writes.

    Each argument is one published document file. The derived splits republish the *same* rows,
    so a document that is relevant and retrieved carries its text three times. Measuring only the
    ``all`` set -- as this did before the splits existed -- under-counts the publication by that
    duplication factor: the pilot publishes 30.1 MB of text while such a check reports 24.7 MB,
    and a run where most rows are relevant could exceed the cap threefold and still pass.
    """
    total_text_bytes = sum(
        len(str(row.get("text") or "").encode("utf-8")) for rows in published for row in rows
    )
    if total_text_bytes > MAX_DOCUMENT_TEXT_BYTES:
        raise PublicationError(
            f"total published text bytes {total_text_bytes} across {len(published)} published "
            f"document file(s) exceeds cap of {MAX_DOCUMENT_TEXT_BYTES} bytes"
        )


def build_plan(
    *,
    repo: str,
    manifest: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    artifacts: Sequence[PublishFile] = (),
) -> PublicationPlan:
    """Everything the publication consists of, as bytes, without touching the Hub.

    ``artifacts`` are the image and PDF bytes for allow-listed sources. They are validated against
    the policy's decisions before anything is planned, so an artifact can only reach the Hub if a
    curated allow-list entry cleared its row.
    """
    _validate_repo(repo)
    _check_card_matches_payload(manifest, artifacts)
    _check_artifacts(artifacts, documents, images)
    relevant_docs = derive_relevant_rows(documents)
    retrieved_docs = derive_retrieved_rows(documents)
    _check_text_byte_cap(documents, relevant_docs, retrieved_docs)
    card = render_card(manifest)
    files = (
        PublishFile(CARD_FILE, card.encode("utf-8")),
        PublishFile(MANIFEST_FILE, canonical_bytes(manifest) + b"\n"),
        PublishFile(DOCUMENTS_FILE, _jsonl(documents)),
        PublishFile(DOCUMENTS_RELEVANT_FILE, _jsonl(relevant_docs)),
        PublishFile(DOCUMENTS_RETRIEVED_FILE, _jsonl(retrieved_docs)),
        PublishFile(IMAGES_FILE, _jsonl(images)),
        *sorted(artifacts, key=lambda file: file.path),
    )
    return PublicationPlan(repo=repo, files=files, manifest=manifest)


def _check_card_matches_payload(
    manifest: Mapping[str, Any], artifacts: Sequence[PublishFile]
) -> None:
    """The card's claim about byte publication must match what is actually uploaded.

    ``publishes_source_bytes`` is derived from the policy's dispositions, while the payload is
    whatever the caller passed. Nothing tied the two together: a cleared row made the card announce
    republished source bytes even when the plan contained only the index, which is a published
    falsehood in either direction.
    """
    claimed = bool(manifest.get("publishes_source_bytes"))
    if claimed and not artifacts:
        raise PublicationError(
            "the manifest claims source bytes are republished, but the plan carries no artifact. "
            "Pass the artifacts, or publish rows the policy did not clear as metadata only."
        )
    if artifacts and not claimed:
        raise PublicationError(
            f"the plan carries {len(artifacts)} artifact(s) while the manifest claims no source "
            "bytes are republished."
        )


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(dict(row)) + b"\n" for row in rows)


#: Files the Hub manages itself. Deleting this would fight the Hub over LFS tracking rules.
HUB_MANAGED_FILES: frozenset[str] = frozenset({".gitattributes"})

#: The only paths a publication may remove: the ones it writes itself.
#:
#: Deleting "everything the plan does not name" is the wrong default for a shared repository. A
#: ``LICENSE``, a ``.gitignore``, an image the card links to, anything a maintainer added through
#: the Hub's web UI -- none of those are ours to remove, and a publication that quietly deletes
#: them is worse than one that leaves a stale file behind. An unrecognised path is left alone.
OWNED_PREFIXES: tuple[str, ...] = ("data/", "images/", "pdfs/")
OWNED_FILES: frozenset[str] = frozenset({CARD_FILE, MANIFEST_FILE})


def _is_ours(path: str) -> bool:
    return path in OWNED_FILES or path.startswith(OWNED_PREFIXES)


def stale_paths(plan: PublicationPlan, remote: Mapping[str, str]) -> list[str]:
    """Remote paths this plan no longer contains, and therefore should stop publishing.

    A publication is a statement of what the dataset *is*, not a list of things to add to it.
    Without this the repository only ever grows: the pilot accumulated 190 loose image files, 3
    PDFs and four JSONL files across successive runs, none of which any later plan mentioned.

    Only paths this stage writes are candidates -- see :data:`OWNED_PREFIXES`. Files the Hub
    manages are excluded too, belt and braces, since ``.gitattributes`` is not under a prefix we
    own and would already be spared.
    """
    planned = {file.path for file in plan.files}
    return sorted(path for path in set(remote) - planned - HUB_MANAGED_FILES if _is_ours(path))


def is_noop(plan: PublicationPlan, remote: Mapping[str, str]) -> bool:
    """Whether the Hub already holds exactly this publication -- no more, no less.

    ``remote`` maps path to whichever content identity the Hub reported -- a git blob id for an
    ordinary file, a SHA-256 for an LFS object. Idempotency is decided by content either way, so
    re-running the same pilot is a no-op no matter how many times it happens.

    Extra remote files count. Ignoring them -- as this did before deletion existed -- meant a
    publication whose whole purpose was removing files reported "already published and identical"
    and removed nothing.
    """
    if stale_paths(plan, remote):
        return False
    return all(file.matches(remote.get(file.path)) for file in plan.files)


#: How each published image column is typed for the Hub. ``image`` is the point of the exercise:
#: without a declared ``image`` dtype the column is inferred as a string and the viewer shows a
#: path instead of a picture.
_IMAGE_DTYPES: Mapping[str, str] = {
    "row_id": "string",
    "pdf_sha256": "string",
    "page_index": "int64",
    "image_index": "int64",
    "sha256": "string",
    "mime": "string",
    "width": "int64",
    "height": "int64",
    "byte_size": "int64",
    "duplicate_of": "string",
    "image": "image",
}


def _image_features() -> str:
    """The ``features`` block for the images config, generated from the published schema.

    Generated rather than written so a new column cannot be added to the rows and forgotten here,
    which would make the declared schema disagree with the data and fail the viewer outright.
    """
    return "\n".join(
        f"      - name: {field}\n        dtype: {_IMAGE_DTYPES[field]}" for field, _ in IMAGE_FIELDS
    )


def _allowlist_table(manifest: Mapping[str, Any]) -> str:
    """The allow list as a card table, generated from the manifest that records it."""
    entries = manifest.get("allowlist") or ()
    if not entries:
        return "_No sources are allow listed, so no bytes are republished._"
    rows = "\n".join(
        f"| `{entry['host']}` | `{entry['identifier']}` | {entry['basis']} |" for entry in entries
    )
    return f"| host | identifier | basis |\n| --- | --- | --- |\n{rows}"


def _table(fields: Sequence[tuple[str, str]]) -> str:
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in fields)
    return f"| field | meaning |\n| --- | --- |\n{rows}"


def render_card(manifest: Mapping[str, Any]) -> str:
    """The dataset card, generated from the manifest and the policy.

    Generated rather than written: the policy and vocabulary sections come from the same functions
    that enforce them, so the published description cannot drift from the behaviour. A card that
    disagrees with the code is worse than no card.
    """
    counts = manifest["counts"]
    source = manifest["source"]
    sampling = manifest["sampling"]
    seed = sampling["seed"]
    rules = manifest["policy"]
    vocabulary = vocabulary_summary()
    counts = manifest["counts"]
    bytes_note = (
        (
            f"**{counts.get('published_pdfs', 0)} source PDF(s) and "
            f"{counts.get('published_images', 0)} image(s) are republished**, from the "
            "allow-listed sources listed under Licensing below. Every other row is "
            "`metadata-only`: the hash and provenance are here, the bytes are not. See the "
            "per-row `disposition`, `pdf` and `image`."
        )
        if manifest["publishes_source_bytes"]
        else (
            "**No source PDF or image bytes are republished.** Every artifact resolved to "
            "`metadata-only`: the hash and provenance are here, the bytes are not."
        )
    )

    return f"""---
configs:
  - config_name: documents
    data_files:
      - split: all
        path: {DOCUMENTS_FILE}
      - split: relevant
        path: {DOCUMENTS_RELEVANT_FILE}
      - split: retrieved
        path: {DOCUMENTS_RETRIEVED_FILE}
  - config_name: images
    data_files:
      - split: train
        path: {IMAGES_FILE}
dataset_info:
  - config_name: images
    features:
{_image_features()}
license: odc-by
task_categories:
- text-classification
language:
- en
tags:
- agriculture
- finepdfs
- proof-of-concept
pretty_name: FinePDFs agriculture pilot (metadata and hashes)
---

# finepdf-to-images — bounded agriculture pilot

A **proof of concept**, not a corpus. It samples one pinned shard of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), scores each row
for agriculture relevance from its extracted text, retrieves only the selected source PDFs,
extracts their embedded images, and publishes **this index**.

{bytes_note}

## Source

| | |
| --- | --- |
| dataset | [`{source["dataset"]}`](https://huggingface.co/datasets/{source["dataset"]}) |
| revision | `{source["revision"]}` |
| config / split / shard | `{source["config"]}` / `{source["split"]}` / `{source["shard"]}` |
| sampling | limit {sampling["limit"]}, strategy `{sampling["strategy"]}`, seed `{seed}` |

The shard holds 388,000 rows in 388 row groups. A run reads only the row groups its
limit requires — never the shard, never the corpus.

## Counts

| | |
| --- | --- |
| documents scored | {counts["documents"]} |
| judged relevant | {counts["relevant"]} |
| PDFs retrieved | {counts["retrieved"]} |
| image references | {counts["images"]} |
| unique images | {counts["unique_images"]} |

## Splits

| config | split | count | description |
| --- | --- | --- | --- |
| `documents` | `all` | {counts["documents"]} | all scored documents from source shard |
| `documents` | `relevant` | {counts["relevant"]} | scored documents judged relevant |
| `documents` | `retrieved` | {counts["retrieved"]} | documents with retrieved source PDF |
| `images` | `train` | {counts["images"]} | extracted images ({counts["unique_images"]} unique) |

## Files

| path | contents |
| --- | --- |
| `{MANIFEST_FILE}` | run manifest: source, sampling, counts, policy, digests |
| `{DOCUMENTS_FILE}` | one row per scored document (split: `all`) |
| `{DOCUMENTS_RELEVANT_FILE}` | documents judged relevant to agriculture (split: `relevant`) |
| `{DOCUMENTS_RETRIEVED_FILE}` | documents whose source PDF was retrieved (split: `retrieved`) |
| `{IMAGES_FILE}` | one row per extracted image |

Digests (SHA-256 over canonical JSON rows):
- `documents` (`all`): `{manifest["documents_digest"][:16]}…`
- `documents` (`relevant`): `{manifest["documents_relevant_digest"][:16]}…`
- `documents` (`retrieved`): `{manifest["documents_retrieved_digest"][:16]}…`
- `images` (`train`): `{manifest["images_digest"][:16]}…`

### `{DOCUMENTS_FILE}`

{_table(DOCUMENT_FIELDS)}

### `{IMAGES_FILE}`

{_table(IMAGE_FIELDS)}

## Relevance

A keyword filter over English text, vocabulary version {vocabulary["version"]}:
{vocabulary["concept_count"]} concepts in {len(vocabulary["groups"])} groups
({", ".join(sorted(vocabulary["groups"]))}), {vocabulary["surface_form_count"]} surface forms.
A document is relevant when it matches at least
{vocabulary["thresholds"]["min_groups"]} groups, or at least
{vocabulary["thresholds"]["min_concepts_in_one_group"]} distinct concepts in one group.

Every relevant row carries `matched_terms`, the exact terms that produced the decision, so you can
disagree with it. Terms deliberately excluded as ambiguous, with reasons, are listed in the
manifest under `policy` and in the repository.

This is not a multilingual classifier and not a model. It cannot recognise a document that never
uses the vocabulary, and it will flag one that mentions farming in passing.

## Licensing and redistribution

{rules["source_attribution"]}

{rules["limitations"]}

Default disposition: **`{rules["default_disposition"]}`**. Bytes are republished only for a
`declared-open` status carrying an allow-listed identifier *and* backed by a human decision
recorded in the source repository.

None of these documents states a licence in its own text. The sources below are open by **statute**
rather than by declaration, which is why each entry cites the instrument it rests on rather than a
licence file. Both ends of a retrieval -- the requested URL and the final URL after
redirects -- must match one of these exactly; a request that leaves the host does not keep
its permission.
Intermediate hops are validated for safety but are not recorded, so they are not checked
against this list.

{_allowlist_table(manifest)}

These are readings of the law, not licences obtained from a rights holder, and the EU Decision in
particular does not extend to third-party material a document may quote. If you hold rights in
anything published here, the takedown route below is honoured without requiring you to prove it.

Every row carries enough provenance — dataset, revision, config, split, shard, row index, row id
and URL — to trace it back to the exact FinePDFs row and source document.

## Takedown

{rules["takedown"]}

## Reproducing this

```bash
git clone https://github.com/NoeFlandre/finepdf-to-images
cd finepdf-to-images && uv sync --locked
uv run finepdf-to-images select --limit {sampling["limit"]} --out out/select
uv run finepdf-to-images score --records out/select/records.jsonl --out out/score
uv run finepdf-to-images retrieve --scored out/score/scored.jsonl \\
  --select-manifest out/select/manifest.json --relevant-only --out out/retrieve
uv run finepdf-to-images extract --retrieved out/retrieve/retrieved.jsonl \\
  --pdf-root out/retrieve --out out/extract
```

Selection, scoring and the manifests are byte-identical across runs. **Retrieval is not**: it
depends on what third-party servers return on the day, and a 2023 crawl's URLs decay. **Extracted
image bytes are not portable across machines** either — Pillow re-encodes to PNG and the deflate
implementation differs by wheel, so hashes differ between platforms. The manifest records the
encoder that produced this run.

## Limitations

- {counts["documents"]} documents from **one shard of one language config**. Not a sample of
  FinePDFs, and nothing here should be read as one.
- English vocabulary only.
- Embedded images only: no page rendering, no OCR, no layout inference.
- Retrieval failures are kept as rows with a `failure_reason`, because a dataset that drops its
  failures cannot be used to reproduce the run.
"""


# --------------------------------------------------------------------------- the minimal dataset


def build_dataset_rows(
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    image_bytes: Mapping[str, bytes] | None = None,
) -> list[dict[str, Any]]:
    """One row per **relevant** document: the published dataset, as plain values.

    Only relevant rows. The 948 rejected documents in the pilot carry no text worth reading and no
    images; publishing them made the dataset look like a pipeline log rather than a corpus. What
    the scorer declined is a fact about the run, and the run's manifest is where it belongs.

    ``matched_terms`` earns its column where the other bookkeeping did not: it is the *reason the
    row exists*. A relevance score of ``3`` communicates nothing on its own, but `soil`,
    `irrigation`, `crop rotation` lets a reader argue with the selection instead of taking it on
    faith.

    ``image_bytes`` maps a digest to the bytes to embed, and holds only digests the policy cleared.
    A row whose images could not be published gets an empty list -- never a broken reference to
    bytes that are not there.
    """
    by_row = _images_by_row(images)
    available = dict(image_bytes or {})
    rows = [
        {
            "pdf_url": _text(document, "url"),
            "text": _text(document, "text"),
            "images": _embedded_images(by_row.get(str(document.get("row_id")), ()), available),
            "matched_terms": [str(term) for term in document.get("matched_terms") or ()],
        }
        for document in derive_relevant_rows(documents)
    ]
    return rows


def _images_by_row(images: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """The extraction index grouped by owning document, each group in page order.

    Reads ``document_row_id``, the raw extraction key -- the published rows rename it to
    ``row_id``, and conflating the two silently yields no images at all.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for image in images:
        grouped.setdefault(str(image.get("document_row_id")), []).append(image)
    for group in grouped.values():
        group.sort(key=lambda image: (_number(image, "page_index"), _number(image, "image_index")))
    return grouped


def _embedded_images(
    images: Sequence[Mapping[str, Any]], available: Mapping[str, bytes]
) -> list[dict[str, Any]]:
    """The embeddable images for one document, each digest once, in first-occurrence order.

    The same bytes can appear on several pages, and the old index emitted a row per occurrence
    with ``duplicate_of`` pointing back at the first. Embedding repeats that way would hand a
    reader the same picture several times, so a digest is carried once.

    ``{"bytes": ..., "path": ...}`` is the shape the Hub's ``Image`` feature decodes; the path is
    a label the viewer shows, not a file that has to exist in the repository.
    """
    embedded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in images:
        digest = str(image.get("sha256") or "")
        if digest in seen or digest not in available:
            continue
        seen.add(digest)
        embedded.append(
            {"bytes": available[digest], "path": image_path(digest, str(image.get("mime") or ""))}
        )
    return embedded


def dataset_image_bytes(
    images: Sequence[Mapping[str, Any]], cleared: frozenset[str]
) -> dict[str, str]:
    """Digest -> mime for the images this publication may embed.

    Thin by design: the decision about *which* images may ship is the policy's, already recorded
    per row, and this reads it rather than re-deriving it. A second implementation of a licensing
    rule is a second thing that can disagree with it.
    """
    return cleared_image_digests(images, cleared)


def build_dataset_plan(
    *, repo: str, manifest: Mapping[str, Any], dataset: PublishFile
) -> PublicationPlan:
    """The whole publication: a card and a parquet, and nothing else.

    ``dataset`` arrives already encoded because parquet is written by an adapter -- the domain
    stays free of ``pyarrow``, whose writer output is an I/O concern and whose version decides the
    published bytes.

    Everything the old layout published that this plan does not name -- the loose images, the
    PDFs, the four JSONL files, ``manifest.json`` -- is removed by the deletion path, since all of
    it sits under paths this stage owns.
    """
    _validate_repo(repo)
    if dataset.path != DATASET_FILE:
        raise PublicationError(
            f"the dataset must be published at {DATASET_FILE!r}, not {dataset.path!r}"
        )
    card = render_dataset_card(manifest, repo)
    files = (PublishFile(CARD_FILE, card.encode("utf-8")), dataset)
    return PublicationPlan(repo=repo, files=files, manifest=manifest)


def _dataset_schema_table() -> str:
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in DATASET_FIELDS)
    return f"| column | meaning |\n| --- | --- |\n{rows}"


def render_dataset_card(manifest: Mapping[str, Any], repo: str) -> str:
    """The card for the minimal dataset.

    The front matter is a machine-read contract, not prose: without a declared ``image`` dtype the
    column is inferred as a string and the viewer shows a struct instead of a picture. It is
    generated from :data:`DATASET_FIELDS` so a column cannot be added to the rows and forgotten
    here, which would make the declared schema disagree with the data and fail the viewer outright.

    Kept deliberately short. Provenance a reader needs to reproduce or cite the run lives here;
    the pipeline explaining itself to its own maintainers belongs in the repository's docs.
    """
    source = manifest["source"]
    sampling = manifest["sampling"]
    counts = manifest["counts"]
    seed = sampling["seed"]
    allowlist = manifest.get("allowlist") or ()
    hosts = ", ".join(f"`{entry['host']}`" for entry in allowlist) or "none"
    return f"""---
configs:
  - config_name: default
    data_files:
      - split: train
        path: {DATASET_FILE}
dataset_info:
  features:
    - name: pdf_url
      dtype: string
    - name: text
      dtype: string
    - name: images
      sequence:
        dtype: image
    - name: matched_terms
      sequence: string
license: odc-by
task_categories:
- text-classification
language:
- en
tags:
- agriculture
- finepdfs
- proof-of-concept
pretty_name: FinePDFs agriculture pilot
---

# finepdf-to-images — agriculture pilot

Agriculture-relevant documents sampled from one pinned shard of
[HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs): the source PDF,
its extracted text, the images embedded in it, and the vocabulary terms that made it relevant.

One row per relevant document ({counts["relevant"]} of {counts["documents"]} scored).

## Schema

{_dataset_schema_table()}

`matched_terms` is why the row is here. It lets you argue with the selection rather than take it
on faith.

## Source

| | |
| --- | --- |
| dataset | [`{source["dataset"]}`](https://huggingface.co/datasets/{source["dataset"]}) |
| revision | `{source["revision"]}` |
| config / split / shard | `{source["config"]}` / `{source["split"]}` / `{source["shard"]}` |
| sampling | limit {sampling["limit"]}, strategy `{sampling["strategy"]}`, seed `{seed}` |

## Licensing

Text is published under **ODC-BY**, inherited from `{source["dataset"]}`, which must be
attributed.

Images are republished **only** from sources separately cleared as free to redistribute
({hosts}). A document whose images were not cleared carries an empty `images` list rather than a
broken reference. To request removal of anything published here, open an issue on the source
repository.

## Use

```python
from datasets import load_dataset

rows = load_dataset("{repo}", split="train")
rows[0]["images"][0]  # a PIL image
```
"""
