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
from finepdf_to_images.domain.images import MIN_IMAGE_SIDE, image_path
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import content_digest, sha256_hex

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

#: ``MANIFEST_FILE`` is no longer published. It survives because ``OWNED_FILES`` needs it so a
#: publication can still delete the manifest.json the six-file layout left on the Hub.
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
    ("pdf_url", "the source PDF this image came from"),
    ("image", "the image itself"),
    ("text", "text extracted from that PDF"),
    ("matched_terms", "the vocabulary terms that made the document relevant"),
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
        # row_index is genuinely nullable -- a row the shard reader could not position -- and the
        # sort below relies on that, so it is read directly rather than through the guards.
        "row_index": scored.get("row_index"),
        "row_id": _text(scored, "row_id"),
        "url": _text(scored, "url"),
        "final_url": _text(retrieval, "final_url"),
        "language": _text(relevance, "language"),
        "text": text,
        "text_sha256": actual_sha256,
        "relevant": _flag(relevance, "relevant"),
        "relevance_score": _number(relevance, "score"),
        "matched_terms": _items(relevance, "matched_terms"),
        "retrieved": _flag(retrieval, "ok"),
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


#: Fields a stage may legitimately omit even when it produced the row at all.
#:
#: Fields a stage writes as an explicit ``null`` when they do not apply to a row.
#:
#: Derived from 234 real retrieval rows rather than guessed: ``reason`` is null for a successful
#: retrieval; ``sha256`` and ``byte_size`` are null for a failed one; the policy blocks are empty
#: until it has ruled. ``final_url`` is *never* null -- it is the requested URL when no redirect
#: happened -- so it is deliberately absent from this set and a null there is a bug.
#:
#: They are *nullable*, not optional. A real stage row carries every key it ever writes — verified
#: against 234 retrieval rows, where the successful and failed rows have identical key sets — so a
#: **missing** key is always a rename or a typo, including for these. Treating them as optional
#: instead let a renamed ``byte_size`` slip through silently, which is the whole bug this guards.
_NULLABLE_FIELDS = frozenset({"reason", "sha256", "byte_size", "license", "disposition", "status"})

_MISSING = object()


def _present(row: Mapping[str, Any], key: str) -> Any:
    """The raw value at ``key``, or a refusal naming the field and what the row actually carries.

    Shared by every reader below so the diagnostic is written once: a renamed field should read
    the same whether it was a string, a number, a flag or a list.
    """
    value = row.get(key, _MISSING)
    if value is _MISSING:
        raise PublicationError(
            f"stage output is missing {key!r}. The row carries {sorted(row)}. "
            "A renamed or misspelled field would publish an empty column, so it is refused here."
        )
    return value


def _read(row: Mapping[str, Any], key: str, kind: type, default: Any) -> Any:
    """One field of a stage output, strict about the difference between absent and wrong.

    **An empty section is legitimate.** A scored document that was never retrieved has no
    retrieval block at all, and no extraction block either, so the caller passes ``{}`` and every
    field falls back to its default. That is the pipeline working.

    **A populated section missing a field is not**, even for a field that is often null. These
    readers used to coerce anything
    unexpected to ``""``, ``0`` or ``{}``, which turned a renamed or misspelled key into a
    plausible published value rather than an error -- an empty column in a public dataset, exit
    code 0. Mutation testing found exactly that: mutants replacing a lookup key survived because
    "the rows kept every documented key and the columns were simply empty".

    So a non-empty section must carry the field, with the right type, unless the field is one a
    stage genuinely writes only sometimes (:data:`_OPTIONAL_FIELDS`).
    """
    if not row:
        return default
    value = _present(row, key)
    # An explicit null is a stage saying "not applicable" for this row -- a successful retrieval
    # writes reason: null rather than dropping the key.
    if value is None and key in _NULLABLE_FIELDS:
        return default
    if isinstance(value, bool) or not isinstance(value, kind):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected {kind.__name__}."
        )
    return value


def _section(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested block. Absent when the stage did not run for this row; never silently wrong."""
    return _read(row, key, Mapping, {})


def _text(row: Mapping[str, Any], key: str) -> str:
    return _read(row, key, str, "")


def _number(row: Mapping[str, Any], key: str) -> int:
    return _read(row, key, int, 0)


def _flag(row: Mapping[str, Any], key: str) -> bool:
    """A boolean field. Separate from :func:`_number` because ``bool`` is a subclass of ``int``."""
    if not row:
        return False
    value = _present(row, key)
    if not isinstance(value, bool):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected bool."
        )
    return value


def _items(row: Mapping[str, Any], key: str) -> list[Any]:
    """A list field, required to be present and a list when the section is populated."""
    if not row:
        return []
    value = _present(row, key)
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise PublicationError(
            f"stage output has {key!r} as {type(value).__name__}, expected a list."
        )
    return list(value)


def derive_relevant_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents judged relevant by the scorer, in shard order."""
    return [dict(row) for row in documents if bool(row.get("relevant"))]


def derive_retrieved_rows(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The subset of documents whose source PDF was successfully retrieved, in shard order."""
    return [dict(row) for row in documents if bool(row.get("retrieved"))]


def cleared_pdf_digests(documents: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Digest -> row id, for the PDFs of cleared rows that actually have one."""
    return {
        str(row["pdf_sha256"]): str(row.get("row_id"))
        for row in documents
        if row.get("disposition") == str(policy.Disposition.PUBLISH_ARTIFACT)
        and _DIGEST_RE.match(str(row.get("pdf_sha256") or ""))
    }


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
    doc_digest = content_digest(list(documents))
    img_digest = content_digest(list(images))
    return {
        "schema_version": SCHEMA_VERSION,
        "source": dict(source),
        "sampling": dict(sampling),
        "vocabulary_version": vocabulary_version,
        "encoder": dict(encoder or {}),
        "counts": _counts(documents, images),
        "publishes_source_bytes": any(
            row.get("disposition") == "publish-artifact" for row in documents
        ),
        "policy": policy.policy_summary(),
        "allowlist": allowlist_summary(),
        "documents_digest": doc_digest,
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


# --------------------------------------------------------------------------- the minimal dataset


def check_text_byte_cap(
    rows: Sequence[Mapping[str, Any]], max_text_bytes: int = MAX_DOCUMENT_TEXT_BYTES
) -> None:
    """Bound the text this publication actually writes.

    The cap guards against dumping unbounded text into a public dataset, and it has to measure
    what is published to do that. It used to sum ``text`` over every **scored** row, which was
    right when the old layout published all of them; once only documents that were retrieved and
    yielded an image reach the Hub, that counted text the run never publishes -- a 5000-row sample
    was refused over 75 MB of which almost all belonged to rejects.

    Measured on the published rows, so the repetition of a document's text across its images (see
    ADR-0016) is counted honestly rather than once per document.
    """
    total = sum(len(str(row.get("text", "")).encode("utf-8")) for row in rows)
    if total > max_text_bytes:
        raise PublicationError(
            f"the {len(rows)} published row(s) carry {total} bytes of text, over the cap of "
            f"{max_text_bytes}. Text repeats across a document's images, so this counts every "
            "published row, not every distinct document."
        )


def check_inputs_match_extraction(
    *,
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    extract_manifest: Mapping[str, Any] | None,
) -> None:
    """Refuse stage inputs that disagree with the extract manifest that describes them.

    Pointing ``--documents`` or ``--images`` at a truncated, empty or stale file used to shrink the
    plan silently. That was survivable while a publication could only add files. Now that it also
    deletes what it does not contain, the same mistake **removes published rows and images from a
    public dataset**, with exit code 0 and no warning -- and an operator passing a stale path is an
    ordinary mistake, not an exotic one.

    The extract stage already records a content digest of exactly these two row sets, so the check
    is an equality rather than a heuristic: it names which file is wrong instead of guessing that
    "too much" is disappearing.

    A run with no extract manifest is not checked. That is the documented way to publish without
    one, and refusing it here would break callers that never had an extraction step.
    """
    if not extract_manifest:
        return
    for label, rows, key in (
        ("--documents", documents, "documents_digest"),
        ("--images", images, "images_digest"),
    ):
        expected = extract_manifest.get(key)
        if expected is None:
            continue
        actual = content_digest([dict(row) for row in rows])
        if actual != expected:
            raise PublicationError(
                f"{label} does not match the extract manifest: it describes {key} "
                f"{expected!r} but the file given hashes to {actual!r}. The file is stale, "
                "truncated, or from another run. Publishing it would delete the published rows "
                "it no longer mentions."
            )


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

    **One row per image, not per document.** A list-of-images column is typed correctly by
    `datasets-server` but the viewer renders it as JSON rather than as pictures, so the dataset's
    whole point was invisible to anyone who had not written code against it. A scalar ``image``
    column renders as a thumbnail. Repeating a document's text across its images costs almost
    nothing once parquet dictionary-compresses it -- 31 MB of logical duplication came to about
    90 KB on the pilot.

    **A document with no image contributes no rows.** This is `finepdf-to-images`: every published
    row carries a picture, by construction rather than by filter.

    ``image_bytes`` maps a digest to the bytes to embed. A digest missing from it cannot be
    embedded, and a document left with nothing embeddable simply produces no rows rather than
    shipping a broken reference.
    """
    by_row = _images_by_row(images)
    available = dict(image_bytes or {})
    rows: list[dict[str, Any]] = []
    for document in derive_relevant_rows(documents):
        embedded = _embedded_images(by_row.get(str(document.get("row_id")), ()), available)
        terms = [str(term) for term in document.get("matched_terms") or ()]
        rows.extend(
            {
                "pdf_url": _text(document, "url"),
                "image": image,
                "text": _text(document, "text"),
                "matched_terms": terms,
            }
            for image in embedded
        )
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


def all_image_digests(images: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Digest -> mime for **every** extracted image, cleared or not.

    The publication policy gates which *PDF* bytes may be redistributed. It no longer gates the
    images: the dataset owner decided that this proof of concept publishes every image it
    extracts, accepting that most sources carry no declared licence. The card states that plainly
    and carries the takedown route, which is the obligation that replaces the filter.

    Keyed by digest because the same image can appear on several pages and in several documents;
    it is embedded once per document that references it.
    """
    return {
        digest: str(image.get("mime") or "")
        for image in images
        if _DIGEST_RE.match(digest := str(image.get("sha256") or ""))
    }


def build_dataset_plan(
    *, repo: str, manifest: Mapping[str, Any], dataset: PublishFile, published_rows: int
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
    card = render_dataset_card(manifest, repo, published_rows)
    files = (PublishFile(CARD_FILE, card.encode("utf-8")), dataset)
    return PublicationPlan(repo=repo, files=files, manifest=manifest)


def _plural(count: int, noun: str) -> str:
    """``noun`` agreeing with ``count``. The card read "1 documents" without it."""
    return noun if count == 1 else f"{noun}s"


def _dataset_schema_table() -> str:
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in DATASET_FIELDS)
    return f"| column | meaning |\n| --- | --- |\n{rows}"


def render_dataset_card(manifest: Mapping[str, Any], repo: str, published_rows: int) -> str:
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
    vocabulary = vocabulary_summary()
    thresholds = vocabulary["thresholds"]
    groups = len(vocabulary["groups"])
    counts = manifest["counts"]
    seed = sampling["seed"]
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
    - name: image
      dtype: image
    - name: text
      dtype: string
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

One row per image: {published_rows} {_plural(published_rows, "image")} extracted from the
documents that {counts["documents"]} scored rows yielded. A document's text and matched terms
repeat across its images, so every row stands alone.

## Schema

{_dataset_schema_table()}

`matched_terms` is why the row is here. It lets you argue with the selection rather than take it
on faith.

## How a row got here

**Selection is a keyword filter over English text — not a model.** It is deliberately
unclever, so you can read the rule, disagree with it, and see exactly which words produced
each row in `matched_terms`.

The text is Unicode-normalised and casefolded, then matched against
**{vocabulary["surface_form_count"]} phrases** grouped into
**{vocabulary["concept_count"]} concepts** across {groups} groups
(crops, soil, irrigation, livestock, fisheries, forestry, farm management).
Spellings of one idea — `fertilizer`/`fertiliser`, `farm`/`farmer`/`farming` — count as **one**
concept, not several, so repetition cannot manufacture evidence.

A document is **relevant** when it matches at least {thresholds["min_groups"]} different groups,
or at least {thresholds["min_concepts_in_one_group"]} distinct concepts inside one. A single
passing mention is not enough. Ambiguous words are excluded outright — `corn`, `crop`, `field`,
`plant`, `yield` and `harvest` mean other things in most documents.

**What this misses:** it only covers English, so an agricultural document in another language is
a miss rather than a negative, and a relevant document that never uses the vocabulary is invisible
to it.

Everything after selection is mechanical: only relevant documents are fetched, only real PDFs are
kept, and images under **{MIN_IMAGE_SIDE}px on either side** are dropped — PDFs embed their table
rules as images and those are not pictures. A document left with no image publishes no rows.

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

**Images are reproduced from their source PDFs and most carry no declared licence.** This is a
research proof of concept, not a cleared redistribution. Each image is included because it
appeared in a document the scorer selected; copyright remains with its original owner.

**Takedown:** if you hold rights to anything published here and want it removed, open an issue at
<https://github.com/NoeFlandre/finepdf-to-images/issues> and it will be taken down promptly. Each
row carries its `pdf_url`, so the source of any image can be identified directly.

## Use

```python
from datasets import load_dataset

rows = load_dataset("{repo}", split="train")
rows[0]["image"]  # a PIL image
```
"""
