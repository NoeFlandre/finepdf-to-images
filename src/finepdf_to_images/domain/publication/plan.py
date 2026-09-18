"""What a publication consists of, and what it would change on the Hub.

A plan is a value: the files to upload and the paths to remove. Nothing here talks to the Hub,
which is what makes "a dry run cannot mutate the remote" a structural fact rather than a
promise -- the dry run simply never reaches the adapter.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from finepdf_to_images.domain import policy
from finepdf_to_images.domain.allowlist import allowlist_summary
from finepdf_to_images.domain.publication.card import render_dataset_card
from finepdf_to_images.domain.publication.schema import (
    _REPO_RE,
    CARD_FILE,
    DATASET_FILE,
    MANIFEST_FILE,
    SCHEMA_VERSION,
    PublicationError,
)
from finepdf_to_images.domain.serialization import content_digest, sha256_hex


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
