"""The ``publish`` stage, against a fake Hub.

No test here constructs the real Hub, so none can reach the network or a credential. The property
that matters most — **a dry run writes nothing** — is asserted on the fake's own call log rather
than inferred from reading the code.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from finepdf_to_images.adapters.hub import FakeHub, HubError
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DOCUMENTS_FILE,
    IMAGES_FILE,
    MANIFEST_FILE,
)
from finepdf_to_images.pipeline import PublicationResult, run_publish
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


def publish(
    hub: FakeHub,
    *,
    apply: bool = False,
    out_dir: pathlib.Path | None = None,
    documents: int = 2,
) -> PublicationResult:
    return run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(i, relevant=i == 0) for i in range(documents)],
        retrieved=[retrieved_row(0)],
        documents=[document_row(0)],
        images=[image_row(0)],
        extract_manifest=EXTRACT_MANIFEST,
        apply=apply,
        out_dir=out_dir,
    )


# --------------------------------------------------------------------------- dry run


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
    assert [file.path for file in result.plan.files] == [
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        IMAGES_FILE,
    ]
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
    assert "No source PDF or image bytes are republished" in (tmp_path / CARD_FILE).read_text()


# --------------------------------------------------------------------------- apply


def test_applying_uploads_every_file_in_one_commit() -> None:
    hub = FakeHub()
    result = publish(hub, apply=True)
    assert result.applied is True
    assert result.ok
    assert set(hub.files) == {CARD_FILE, MANIFEST_FILE, DOCUMENTS_FILE, IMAGES_FILE}
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
        def upload(self, plan, message):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message)
            self.files.pop(DOCUMENTS_FILE, None)
            return revision

    result = publish(LosesAFile(), apply=True)
    assert not result.ok
    assert result.missing == (DOCUMENTS_FILE,)


# --------------------------------------------------------------------------- idempotency


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


# --------------------------------------------------------------------------- failures


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
    assert set(hub.files) == {CARD_FILE, MANIFEST_FILE, DOCUMENTS_FILE, IMAGES_FILE}
    assert hub.files[DOCUMENTS_FILE] == b""


# --------------------------------------------------------------------------- published content


def test_the_published_rows_are_canonical_jsonl() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    lines = hub.files[DOCUMENTS_FILE].decode().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert list(row) == sorted(row), "canonical JSON sorts its keys"


def test_the_published_manifest_is_readable_json_with_the_source_pinned() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    manifest = json.loads(hub.files[MANIFEST_FILE])
    assert manifest["source"]["revision"] == SOURCE["revision"]
    assert manifest["publishes_source_bytes"] is False
    assert manifest["encoder"]["pypdf"] == "6.19.0"


def test_no_published_file_contains_anything_resembling_a_credential() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    for path, data in hub.files.items():
        text = data.decode("utf-8", errors="replace").lower()
        for secret in ("hf_", "token=", "authorization", "api_key", "bearer "):
            assert secret not in text, f"{path} may contain a credential"


# --------------------------------------------------------------------------- CLI


def run_cli(args: list[str]) -> int:
    from finepdf_to_images.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def staged_inputs(tmp_path: pathlib.Path) -> list[str]:
    def write(name: str, payload: object) -> str:
        path = tmp_path / name
        if isinstance(payload, list):
            path.write_text("".join(json.dumps(row) + "\n" for row in payload), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    return [
        "--select-manifest",
        write("select.json", SELECT_MANIFEST),
        "--scored",
        write("scored.jsonl", [scored_row(0)]),
        "--retrieved",
        write("retrieved.jsonl", [retrieved_row(0)]),
        "--documents",
        write("documents.jsonl", [document_row(0)]),
        "--images",
        write("images.jsonl", [image_row(0)]),
        "--extract-manifest",
        write("extract.json", EXTRACT_MANIFEST),
        "--repo",
        "a/b",
    ]


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
        def upload(self, plan, message):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message)
            self.files.pop(DOCUMENTS_FILE, None)
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
    assert not result.ok, "unverifiable files must be reported, not assumed correct"
    assert set(result.missing) == {CARD_FILE, MANIFEST_FILE, DOCUMENTS_FILE, IMAGES_FILE}


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
