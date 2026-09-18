"""What a publication plans, applies and verifies."""

from __future__ import annotations

import pathlib

import pytest

from finepdf_to_images.adapters.hub import FakeHub, HubError
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DATASET_FILE,
    MANIFEST_FILE,
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


def test_a_dry_run_writes_nothing_to_the_hub() -> None:
    """The acceptance criterion, asserted on the Hub's own call log."""
    hub = FakeHub()
    result = publish(hub)
    assert result.applied is False
    assert hub.wrote is False
    assert hub.files == {}
    assert hub.commits == []


def test_a_dry_run_reports_the_exact_files_and_counts() -> None:
    result = publish(FakeHub())
    assert [file.path for file in result.plan.files] == [CARD_FILE, DATASET_FILE]
    assert result.plan.manifest["counts"]["documents"] == 2
    assert result.plan.manifest["counts"]["relevant"] == 1
    assert all(file.size > 0 and len(file.sha256) == 64 for file in result.plan.files)


def test_a_dry_run_can_write_the_planned_files_locally_for_review(
    tmp_path: pathlib.Path,
) -> None:
    """A reviewer should be able to read the card before anything reaches the Hub."""
    hub = FakeHub()
    result = publish(hub, out_dir=tmp_path)
    assert hub.wrote is False
    for file in result.plan.files:
        local = tmp_path / file.path
        assert local.is_file()
        assert local.read_bytes() == file.data
    assert "# finepdf-to-images" in (tmp_path / CARD_FILE).read_text()


def test_applying_uploads_every_file_in_one_commit() -> None:
    hub = FakeHub()
    result = publish(hub, apply=True)
    assert result.applied is True
    assert result.ok
    assert set(hub.files) == {CARD_FILE, DATASET_FILE}
    assert len(hub.commits) == 1, "a half-updated published state must not be possible"
    assert result.revision == hub.head


def test_the_commit_message_describes_the_run() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    message = hub.commits[0]
    assert "2 documents" in message
    assert SOURCE["revision"] in message


def test_publication_verifies_what_it_uploaded() -> None:
    hub = FakeHub()
    result = publish(hub, apply=True)
    assert set(result.verified) == set(hub.files)
    assert result.missing == ()


def test_a_failed_verification_is_reported_not_swallowed() -> None:
    """If the Hub does not hold what we sent, the run must say so."""

    class LosesAFile(FakeHub):
        def upload(self, plan, message, delete=()):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message, delete)
            self.files.pop(DATASET_FILE, None)
            return revision

    result = publish(LosesAFile(), apply=True)
    assert not result.ok
    assert result.missing == (DATASET_FILE,)


def test_publishing_the_same_result_twice_is_an_exact_noop() -> None:
    hub = FakeHub()
    first = publish(hub, apply=True)
    assert first.applied
    second = publish(hub, apply=True)
    assert second.noop is True
    assert second.applied is False
    assert len(hub.commits) == 1, "a no-op must not create a second commit"
    assert second.revision == first.revision


def test_a_changed_result_is_not_a_noop() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    changed = publish(hub, apply=True, documents=3)
    assert changed.noop is False
    assert changed.applied is True
    assert len(hub.commits) == 2


def test_a_dry_run_after_publishing_reports_the_noop() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    assert publish(hub).noop is True


def test_repeated_dry_runs_produce_the_same_plan() -> None:
    assert publish(FakeHub()).plan.digest == publish(FakeHub()).plan.digest


def test_a_hub_read_failure_surfaces_rather_than_publishing_blindly() -> None:
    hub = FakeHub(fail_with=HubError("network is down"))
    with pytest.raises(HubError):
        publish(hub, apply=True)
    assert hub.wrote is False


def test_publishing_an_empty_run_still_produces_a_coherent_dataset() -> None:
    hub = FakeHub()
    result = run_publish(
        hub=hub,
        repo="a/b",
        select_manifest=SELECT_MANIFEST,
        scored=[],
        retrieved=[],
        documents=[],
        images=[],
        apply=True,
    )
    assert result.plan.manifest["counts"]["documents"] == 0
    assert set(hub.files) == {CARD_FILE, DATASET_FILE}
    assert _published_rows(hub) == [], "an empty run publishes a readable, empty table"


def test_the_published_table_has_exactly_the_reader_facing_columns() -> None:
    """18 columns of pipeline bookkeeping is what this layout exists to stop publishing.

    `caption` joined them in #62: it is the one line written about *this picture*, which is what
    an image-text pair needs and what the document-wide `text` column cannot give.
    """
    hub = FakeHub()
    publish(hub, apply=True)
    rows = _published_rows(hub)
    assert [list(row) for row in rows] == [["pdf_url", "image", "caption", "text", "matched_terms"]]


def test_only_relevant_documents_are_published() -> None:
    """948 of 1000 pilot rows were rejects a reader has no use for."""
    hub = FakeHub()
    result = publish(hub, apply=True, documents=5)
    assert result.plan.manifest["counts"]["documents"] == 5
    assert len(_published_rows(hub)) == 1


def test_matched_terms_survive_into_the_published_row() -> None:
    """The column that lets a reader argue with the selection instead of trusting it."""
    hub = FakeHub()
    publish(hub, apply=True)
    assert _published_rows(hub)[0]["matched_terms"] == ["maize", "irrigation"]


def test_the_card_pins_the_source_revision_now_that_no_manifest_is_published() -> None:
    """Provenance moved to the card; it must not simply disappear with manifest.json."""
    hub = FakeHub()
    publish(hub, apply=True)
    card = hub.files[CARD_FILE].decode()
    assert SOURCE["revision"] in card
    assert SOURCE["dataset"] in card
    assert MANIFEST_FILE not in hub.files


def test_no_published_file_contains_anything_resembling_a_credential() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    for path, data in hub.files.items():
        text = data.decode("utf-8", errors="replace").lower()
        for secret in ("hf_", "token=", "authorization", "api_key", "bearer "):
            assert secret not in text, f"{path} may contain a credential"
