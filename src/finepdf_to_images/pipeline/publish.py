"""Planning and carrying out a publication."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finepdf_to_images.adapters.hub import Hub
from finepdf_to_images.adapters.parquet import encode_dataset
from finepdf_to_images.adapters.storage import write_bytes
from finepdf_to_images.domain.images import (
    image_path,
)
from finepdf_to_images.domain.publication import (
    PublicationError,
    PublicationPlan,
    PublishFile,
    all_image_digests,
    build_dataset_plan,
    build_dataset_rows,
    build_document_rows,
    build_image_rows,
    check_inputs_match_extraction,
    check_text_byte_cap,
    cleared_pdf_digests,
    is_noop,
    stale_paths,
)
from finepdf_to_images.domain.publication import build_manifest as build_publication_manifest
from finepdf_to_images.domain.retrieval import (
    artifact_path,
)
from finepdf_to_images.domain.scoring import vocabulary_summary
from finepdf_to_images.domain.serialization import (
    sha256_hex,
)


@dataclass(frozen=True, slots=True)
class PublicationResult:
    plan: PublicationPlan
    applied: bool
    noop: bool
    revision: str
    verified: tuple[str, ...]
    missing: tuple[str, ...]
    #: Published files the plan no longer contains: removed in the same commit as the writes when
    #: ``apply`` is set, and merely reported on a dry run.
    removed: tuple[str, ...] = ()
    #: Paths that should have been removed and are still served when the Hub is read back.
    remaining: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing and not self.remaining


def run_publish(
    *,
    hub: Hub,
    repo: str,
    select_manifest: Mapping[str, Any],
    scored: Sequence[Mapping[str, Any]],
    retrieved: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    extract_manifest: Mapping[str, Any] | None = None,
    apply: bool = False,
    out_dir: pathlib.Path | None = None,
    pdf_root: pathlib.Path | None = None,
    image_root: pathlib.Path | None = None,
) -> PublicationResult:
    """Plan a publication, and carry it out only when ``apply`` is true.

    The dry run never reaches the adapter's write path. That is the structural reason "dry-run does
    not mutate the Hub" holds: it is not a flag consulted inside an upload, it is an upload that is
    never called.
    """
    plan, manifest = _plan_publication(
        repo=repo,
        select_manifest=select_manifest,
        scored=scored,
        retrieved=retrieved,
        documents=documents,
        images=images,
        extract_manifest=extract_manifest,
        pdf_root=pdf_root,
        image_root=image_root,
    )
    if out_dir is not None:
        # A local copy of exactly what would be uploaded, so a reviewer can read the card and the
        # rows before anything reaches the Hub.
        for file in plan.files:
            write_bytes(out_dir / file.path, file.data)

    # Read once: a second read could see a different remote, and then the no-op decision and the
    # deletion list would be derived from two different views of the same repository.
    remote = hub.file_digests(repo)
    noop = is_noop(plan, remote)
    stale = tuple(stale_paths(plan, remote))
    if not apply:
        return PublicationResult(
            plan=plan,
            applied=False,
            noop=noop,
            revision="",
            verified=(),
            missing=(),
            removed=stale,
        )
    if noop:
        return _already_published(hub, plan, repo)
    return _upload_and_verify(hub, plan, repo, manifest, stale)


def _already_published(hub: Hub, plan: PublicationPlan, repo: str) -> PublicationResult:
    """Nothing to do: every file is on the Hub with exactly these bytes."""
    return PublicationResult(
        plan=plan,
        applied=False,
        noop=True,
        revision=hub.revision(repo),
        verified=tuple(file.path for file in plan.files),
        missing=(),
    )


def _upload_and_verify(
    hub: Hub,
    plan: PublicationPlan,
    repo: str,
    manifest: Mapping[str, Any],
    stale: Sequence[str] = (),
) -> PublicationResult:
    """Publish, then read the Hub back and check it holds exactly what we sent.

    ``stale`` is removed in the same commit, so the published revision is never a mixture of the
    old shape and the new one.
    """
    revision = hub.upload(plan, _commit_message(manifest), stale)
    published = hub.file_digests(repo)
    # Both halves of the commit are verified: a deletion that did not happen leaves the dataset
    # serving the old shape while the run reports success.
    remaining = tuple(path for path in stale if path in published)
    missing = _unverified(plan, stale, published)
    return PublicationResult(
        plan=plan,
        applied=True,
        noop=False,
        revision=revision,
        verified=tuple(file.path for file in plan.files if file.path not in missing),
        missing=missing,
        removed=tuple(stale),
        remaining=remaining,
    )


def _unverified(
    plan: PublicationPlan, stale: Sequence[str], published: Mapping[str, str]
) -> tuple[str, ...]:
    """Everything the Hub does not hold as this publication says it should.

    Both halves of the commit, not just the half that adds: a deletion that did not happen is as
    much a failed publication as a write that did not, because the dataset would keep serving the
    old shape while the run reported success.
    """
    written = [file.path for file in plan.files if not file.matches(published.get(file.path))]
    still_there = [path for path in stale if path in published]
    return tuple(written + still_there)


def _plan_publication(
    *,
    repo: str,
    select_manifest: Mapping[str, Any],
    scored: Sequence[Mapping[str, Any]],
    retrieved: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
    images: Sequence[Mapping[str, Any]],
    extract_manifest: Mapping[str, Any] | None,
    pdf_root: pathlib.Path | None = None,
    image_root: pathlib.Path | None = None,
) -> tuple[PublicationPlan, dict[str, Any]]:
    """Assemble everything a publication would write, without touching the Hub."""
    # Before anything is planned: a stale or truncated stage file would shrink the plan, and a
    # publication deletes what it does not contain.
    check_inputs_match_extraction(
        documents=documents, images=images, extract_manifest=extract_manifest
    )
    document_rows = build_document_rows(scored=scored, retrieved=retrieved, extracted=documents)
    # Every extracted image is published, not only the policy-cleared ones: the dataset owner
    # decided this pilot ships what it extracts, and the card carries the takedown route in place
    # of the filter. Still not conditioned on ``image_root`` -- what the run extracted is a fact
    # about the run, not about which paths the caller happened to pass.
    shipped_images = all_image_digests(images)
    image_rows = build_image_rows(images, shipped_images)
    manifest = build_publication_manifest(
        source=select_manifest["source"],
        sampling=select_manifest["sampling"],
        documents=document_rows,
        images=image_rows,
        vocabulary_version=vocabulary_summary()["version"],
        encoder=(extract_manifest or {}).get("encoder"),
    )
    artifacts = _load_artifacts(
        documents=document_rows,
        shipped_images=shipped_images,
        pdf_root=pdf_root,
        image_root=image_root,
    )
    # The artifacts were read and digest-verified above, so the bytes embedded in a row are the
    # same bytes, checked the same way, that the old layout published as loose files.
    embeddable = {
        digest: artifact.data
        for digest, artifact in _artifacts_by_digest(artifacts, shipped_images).items()
    }
    dataset_rows = build_dataset_rows(document_rows, images, embeddable)
    check_text_byte_cap(dataset_rows)
    dataset = encode_dataset(dataset_rows)
    # The card reports what the table holds, not what the scorer judged relevant. Those diverged
    # once documents without an image stopped being published, and the card said 52 where the
    # dataset had 10.
    plan = build_dataset_plan(
        repo=repo, manifest=manifest, dataset=dataset, published_rows=len(dataset_rows)
    )
    return plan, manifest


def _artifacts_by_digest(
    artifacts: Sequence[PublishFile], shipped_images: Mapping[str, str]
) -> dict[str, PublishFile]:
    """The image artifacts, keyed by digest.

    Only images: a PDF is no longer republished as bytes, so its artifact has no column to land
    in. Keyed off the digest the policy cleared rather than off the path, so a path convention
    change cannot quietly empty this mapping.
    """
    by_path = {artifact.path: artifact for artifact in artifacts}
    found: dict[str, PublishFile] = {}
    for digest, mime in shipped_images.items():
        artifact = by_path.get(image_path(digest, mime))
        if artifact is not None:
            found[digest] = artifact
    return found


def _load_artifacts(
    *,
    documents: Sequence[Mapping[str, Any]],
    shipped_images: Mapping[str, str],
    pdf_root: pathlib.Path | None,
    image_root: pathlib.Path | None,
) -> list[PublishFile]:
    """Read the bytes for every cleared artifact, verifying each against its own digest.

    The digest is re-computed from the bytes on disk rather than trusted from the index. The index
    and the file can disagree -- a truncated write, an edited working copy -- and publishing under
    a digest the bytes do not have would break the one guarantee content addressing offers.
    """
    pdfs = cleared_pdf_digests(documents)
    artifacts: list[PublishFile] = []

    if pdfs:
        root = _required_root(pdf_root, len(pdfs), "--pdf-root", "PDF")
        for digest, row_id in sorted(pdfs.items()):
            artifacts.append(
                _artifact(root / artifact_path(digest), digest, artifact_path(digest), row_id)
            )
    if shipped_images:
        root = _required_root(image_root, len(shipped_images), "--image-root", "image")
        for digest, mime in sorted(shipped_images.items()):
            path = image_path(digest, mime)
            artifacts.append(_artifact(root / path, digest, path, digest))
    return artifacts


def _required_root(root: pathlib.Path | None, cleared: int, flag: str, kind: str) -> pathlib.Path:
    """Refuse to publish a smaller plan because a path was forgotten.

    Omitting a root used to quietly drop those artifacts from the plan. That was survivable while
    publication could only add files: the bytes simply were not uploaded that run. Now that a
    publication also deletes what it does not contain, the same forgotten flag would **remove**
    already-published bytes from a public dataset -- silently, with exit code 0.

    So a missing root is an error whenever the policy cleared anything. Publishing metadata only
    is still possible; it is expressed by clearing nothing, not by forgetting an argument.
    """
    if root is None:
        raise PublicationError(
            f"{cleared} {kind}(s) are cleared for publication but {flag} was not given. "
            f"Pass {flag}, or the publication would drop them from the plan -- and a publication "
            "deletes what it does not contain."
        )
    return root


def _artifact(source: pathlib.Path, digest: str, path: str, owner: str) -> PublishFile:
    data = source.read_bytes()
    actual = sha256_hex(data)
    if actual != digest:
        raise PublicationError(
            f"artifact for {owner} is {actual} on disk but indexed as {digest}: refusing to "
            f"publish bytes under a digest they do not have ({source})"
        )
    return PublishFile(path, data)


def _commit_message(manifest: Mapping[str, Any]) -> str:
    """A commit message derived from the run, so two runs of the same pilot commit identically."""
    counts = manifest["counts"]
    source = manifest["source"]
    return (
        f"Publish bounded FinePDFs agriculture pilot: {counts['documents']} documents, "
        f"{counts['relevant']} relevant, {counts['retrieved']} retrieved, "
        f"{counts['images']} images\n\n"
        f"Source: {source['dataset']}@{source['revision']} {source['path']}"
    )
