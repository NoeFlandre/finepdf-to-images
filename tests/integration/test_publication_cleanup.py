"""Removing what a publication no longer contains."""

from __future__ import annotations

import pathlib

import pytest

from finepdf_to_images.adapters.hub import FakeHub
from finepdf_to_images.domain.publication import (
    PublicationError,
)
from finepdf_to_images.pipeline import run_publish
from tests.integration.publication_support import (  # noqa: F401
    _card,
    _cleared_run,
    _fixture_image_root,
    _manifest_for,
    _publish_cleared,
    _published_rows,
    publish,
    run_cli,
    staged_inputs,
)
from tests.unit.test_publication_domain import (
    SAMPLING,
    SOURCE,
    document_row,
    image_row,
    retrieved_row,
    scored_row,
)

pytestmark = pytest.mark.integration

SELECT_MANIFEST = {"stage": "select", "source": SOURCE, "sampling": SAMPLING}

EXTRACT_MANIFEST = {"stage": "extract", "encoder": {"pypdf": "6.19.0", "pillow": "12.3.0"}}

CLEARED_PUBLICATION = {
    "disposition": "publish-artifact",
    "reason": "public-domain confirmed by curated-allowlist",
    "license": {
        "status": "declared-open",
        "identifier": "public-domain",
        "evidence": "curated-allowlist",
        "note": "ntp.niehs.nih.gov: 17 U.S.C. 105",
    },
}


def test_stale_remote_files_are_deleted_when_publishing() -> None:
    """The repository accumulated 203 files no plan mentioned, because upload could only add."""
    hub = FakeHub(files={"data/old.jsonl": b"{}", "images/ab/cd/x.png": b"\x89PNG"})
    result = publish(hub, apply=True)

    assert result.applied
    assert sorted(hub.deleted) == ["data/old.jsonl", "images/ab/cd/x.png"]
    assert "data/old.jsonl" not in hub.files
    assert "images/ab/cd/x.png" not in hub.files


def test_the_cleanup_happens_in_the_same_commit_as_the_writes() -> None:
    """Two commits would leave a revision that is neither the old dataset nor the new one."""
    hub = FakeHub(files={"data/old.jsonl": b"{}"})
    publish(hub, apply=True)
    assert len(hub.commits) == 1


def test_gitattributes_is_never_deleted() -> None:
    hub = FakeHub(files={".gitattributes": b"* text=auto"})
    publish(hub, apply=True)
    assert hub.deleted == []
    assert ".gitattributes" in hub.files


def test_a_dry_run_deletes_nothing() -> None:
    """Deletion must not become the one operation that escapes the dry run."""
    hub = FakeHub(files={"data/old.jsonl": b"{}"})
    result = publish(hub)

    assert not result.applied
    assert hub.deleted == []
    assert hub.files == {"data/old.jsonl": b"{}"}
    assert not hub.wrote


def test_a_remote_with_stale_files_is_not_reported_as_already_published() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    hub.files["data/left-behind.jsonl"] = b"{}"

    second = publish(hub, apply=True)
    assert not second.noop, "a cleanup must not be mistaken for a no-op"
    assert second.applied
    assert "data/left-behind.jsonl" not in hub.files


def test_publishing_twice_with_nothing_stale_is_still_a_no_op() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    commits = len(hub.commits)
    second = publish(hub, apply=True)

    assert second.noop
    assert len(hub.commits) == commits
    assert hub.deleted == []


def test_forgetting_only_the_image_root_is_refused(tmp_path: pathlib.Path) -> None:
    """The dangerous half: with --pdf-root given, the PDFs satisfied every other check.

    `publishes_source_bytes` stayed true, the card/payload agreement passed on the PDFs alone,
    and every published image became stale and was deleted. Exit code 0.
    """
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    with pytest.raises(PublicationError, match="--image-root was not given"):
        run_publish(
            hub=FakeHub(),
            repo="NoeFlandre/finepdf-to-images-poc",
            select_manifest=SELECT_MANIFEST,
            scored=run["scored"],
            retrieved=run["retrieved"],
            documents=run["documents"],
            images=run["images"],
            extract_manifest=EXTRACT_MANIFEST,
            pdf_root=run["pdf_root"],
        )


def test_a_run_that_clears_nothing_needs_no_roots(tmp_path: pathlib.Path) -> None:
    """Publishing metadata only is expressed by clearing nothing, not by forgetting an argument."""
    result = publish(FakeHub())
    assert not result.plan.manifest["publishes_source_bytes"]
    assert [
        file.path for file in result.plan.files if file.path.startswith(("pdfs/", "images/"))
    ] == []


def test_a_deletion_that_did_not_happen_is_a_failed_publication() -> None:
    """Verification used to cover only the writes. A stale file still being served is a failure:
    the dataset would keep the old shape while the run reported success."""

    class KeepsAFile(FakeHub):
        def upload(self, plan, message, delete=()):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message, ())
            self.deleted.extend(delete)
            return revision

    hub = KeepsAFile(files={"data/old.jsonl": b"{}"})
    result = publish(hub, apply=True)

    assert not result.ok
    assert "data/old.jsonl" in result.missing


def test_a_file_outside_the_paths_we_publish_is_left_alone() -> None:
    """A LICENSE, a .gitignore, an asset a maintainer added by hand -- none are ours to remove."""
    hub = FakeHub(files={"LICENSE": b"MIT", "assets/logo.png": b"\x89PNG", "data/old.jsonl": b"{}"})
    publish(hub, apply=True)

    assert hub.deleted == ["data/old.jsonl"]
    assert hub.files["LICENSE"] == b"MIT"
    assert hub.files["assets/logo.png"] == b"\x89PNG"


def test_a_truncated_documents_file_is_refused_rather_than_shrinking_the_plan() -> None:
    """Constructed as the mistake, not as a flag assertion.

    An operator points --documents at a stale file holding one fewer row. Publishing it would
    delete the published rows it no longer mentions, with exit code 0.
    """
    documents = [document_row(0), document_row(1)]
    images = [image_row(0)]
    manifest = _manifest_for(documents, images)

    with pytest.raises(PublicationError, match="--documents does not match the extract manifest"):
        run_publish(
            hub=FakeHub(),
            repo="a/b",
            select_manifest=SELECT_MANIFEST,
            scored=[scored_row(0, relevant=True)],
            retrieved=[retrieved_row(0)],
            documents=documents[:-1],
            images=images,
            extract_manifest=manifest,
            image_root=_fixture_image_root(),
        )


def test_an_empty_images_file_is_refused() -> None:
    """The reviewer's demonstrated case: empty images deleted every published image, ok=True."""
    documents = [document_row(0)]
    images = [image_row(0)]
    manifest = _manifest_for(documents, images)

    with pytest.raises(PublicationError, match="--images does not match the extract manifest"):
        run_publish(
            hub=FakeHub(),
            repo="a/b",
            select_manifest=SELECT_MANIFEST,
            scored=[scored_row(0, relevant=True)],
            retrieved=[retrieved_row(0)],
            documents=documents,
            images=[],
            extract_manifest=manifest,
        )


def test_inputs_that_match_the_extract_manifest_publish_normally() -> None:
    """The guard must not refuse a correct run -- it is an equality, not a heuristic."""
    documents = [document_row(0)]
    images = [image_row(0)]
    hub = FakeHub()
    result = run_publish(
        hub=hub,
        repo="a/b",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(0, relevant=True)],
        retrieved=[retrieved_row(0)],
        documents=documents,
        images=images,
        extract_manifest=_manifest_for(documents, images),
        image_root=_fixture_image_root(),
        apply=True,
    )
    assert result.ok
    assert len(_published_rows(hub)) == 1


def test_a_run_without_an_extract_manifest_is_still_allowed() -> None:
    """Publishing without an extraction step is documented; the guard must not break it."""
    hub = FakeHub()
    result = publish(hub, apply=True)
    assert result.ok


def test_the_refusal_names_which_file_is_wrong() -> None:
    """A good error beats a heuristic: it says which path to look at, and why."""
    documents = [document_row(0)]
    images = [image_row(0)]
    with pytest.raises(PublicationError) as caught:
        run_publish(
            hub=FakeHub(),
            repo="a/b",
            select_manifest=SELECT_MANIFEST,
            scored=[scored_row(0, relevant=True)],
            retrieved=[retrieved_row(0)],
            documents=documents,
            images=[],
            extract_manifest=_manifest_for(documents, images),
        )
    message = str(caught.value)
    assert "--images" in message
    assert "stale, truncated, or from another run" in message
