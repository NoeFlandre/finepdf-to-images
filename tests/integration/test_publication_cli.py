"""The publish command's CLI contract."""

from __future__ import annotations

import json
import pathlib

import pytest

from finepdf_to_images.adapters.hub import FakeHub, HubError
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DATASET_FILE,
)
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


def test_cli_dry_run_says_so_and_touches_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images import cli

    hub = FakeHub()
    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: hub)
    assert run_cli(["publish", *staged_inputs(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "would publish" in out
    assert hub.wrote is False


def test_cli_apply_publishes_and_prints_the_revision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images import cli

    hub = FakeHub()
    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: hub)
    assert run_cli(["publish", *staged_inputs(tmp_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "published at revision" in out
    assert "huggingface.co/datasets/a/b" in out
    assert hub.wrote is True


def test_cli_apply_twice_reports_a_noop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images import cli

    hub = FakeHub()
    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: hub)
    args = staged_inputs(tmp_path)
    run_cli(["publish", *args, "--apply"])
    capsys.readouterr()
    run_cli(["publish", *args, "--apply"])
    assert "no-op" in capsys.readouterr().out
    assert len(hub.commits) == 1


def test_cli_refuses_an_invalid_destination(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finepdf_to_images import cli

    monkeypatch.setattr(cli, "HUB_FACTORY", FakeHub)
    args = staged_inputs(tmp_path)
    args[args.index("--repo") + 1] = "not-a-repo"
    assert run_cli(["publish", *args]) == 2


def test_cli_refuses_the_wrong_select_manifest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images import cli

    monkeypatch.setattr(cli, "HUB_FACTORY", FakeHub)
    args = staged_inputs(tmp_path)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"stage": "score"}), encoding="utf-8")
    args[args.index("--select-manifest") + 1] = str(wrong)
    assert run_cli(["publish", *args]) == 1
    assert "not a select manifest" in capsys.readouterr().err


def test_cli_reports_a_failed_verification_as_a_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If the Hub does not hold what we sent, the command must fail rather than claim success."""
    from finepdf_to_images import cli

    class LosesAFile(FakeHub):
        def upload(self, plan, message, delete=()):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message, delete)
            self.files.pop(DATASET_FILE, None)
            return revision

    monkeypatch.setattr(cli, "HUB_FACTORY", LosesAFile)
    assert run_cli(["publish", *staged_inputs(tmp_path), "--apply"]) == 1
    assert "verification FAILED" in capsys.readouterr().err


def test_cli_dry_run_after_publishing_says_it_is_already_published(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images import cli

    hub = FakeHub()
    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: hub)
    args = staged_inputs(tmp_path)
    run_cli(["publish", *args, "--apply"])
    capsys.readouterr()
    run_cli(["publish", *args])
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "already published and identical" in out


def test_cli_can_write_the_planned_files_for_review(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finepdf_to_images import cli

    hub = FakeHub()
    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: hub)
    out = tmp_path / "review"
    run_cli(["publish", *staged_inputs(tmp_path), "--out", str(out)])
    assert (out / CARD_FILE).is_file()
    assert hub.wrote is False


def test_publication_is_verified_and_idempotent_against_a_realistic_hub() -> None:
    """The end-to-end property the LFS bug broke, against a Hub that reports what the real one does.

    REGRESSION: FakeHub used to return a content SHA-256 for every file -- behaviour the real
    adapter never exhibits -- so every idempotency and verification test asserted a property that
    could not hold in production. This exercises the same path through git blob ids.
    """
    hub = FakeHub()
    first = publish(hub, apply=True)
    assert first.ok, "a successful publication must not report a verification failure"
    assert first.missing == ()

    second = publish(hub, apply=True)
    assert second.noop is True
    assert len(hub.commits) == 1

    assert publish(hub).noop is True, "a later dry run must recognise the published state"


def test_a_hub_that_reports_nothing_is_never_a_noop() -> None:
    """A Hub with no usable identity for a file must re-publish, not silently skip it."""

    class Silent(FakeHub):
        def file_digests(self, repo: str) -> dict[str, str]:
            self.calls.append(f"file_digests:{repo}")
            return {}

    hub = Silent()
    result = publish(hub, apply=True)
    assert result.noop is False
    assert set(result.missing) == {CARD_FILE, DATASET_FILE}


def test_cli_reports_a_hub_failure_as_a_diagnostic_not_a_traceback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dead network or a wrong repo name is a failure to publish, not a crash."""
    from finepdf_to_images import cli

    monkeypatch.setattr(cli, "HUB_FACTORY", lambda: FakeHub(fail_with=HubError("network is down")))
    assert run_cli(["publish", *staged_inputs(tmp_path)]) == 1
    assert "network is down" in capsys.readouterr().err


def test_cli_reports_a_truncated_select_manifest_by_name(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The card and commit message index the source and sampling blocks directly; a missing one
    should name the file rather than surface as a KeyError from card rendering."""
    from finepdf_to_images import cli

    monkeypatch.setattr(cli, "HUB_FACTORY", FakeHub)
    args = staged_inputs(tmp_path)
    truncated = tmp_path / "truncated.json"
    truncated.write_text(json.dumps({"stage": "select", "source": SOURCE}), encoding="utf-8")
    args[args.index("--select-manifest") + 1] = str(truncated)
    assert run_cli(["publish", *args]) == 1
    assert "no usable 'sampling' block" in capsys.readouterr().err


def test_cli_names_the_missing_field_in_a_truncated_source_block(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The card and commit message index source["path"] and friends directly; a bare
    `KeyError: 'path'` printed without the filename is a worse diagnostic than naming it."""
    from finepdf_to_images import cli

    monkeypatch.setattr(cli, "HUB_FACTORY", FakeHub)
    args = staged_inputs(tmp_path)
    truncated = tmp_path / "partial.json"
    source = {key: value for key, value in SOURCE.items() if key != "path"}
    truncated.write_text(
        json.dumps({"stage": "select", "source": source, "sampling": SAMPLING}), encoding="utf-8"
    )
    args[args.index("--select-manifest") + 1] = str(truncated)
    assert run_cli(["publish", *args]) == 1
    assert "'source' is missing path" in capsys.readouterr().err
