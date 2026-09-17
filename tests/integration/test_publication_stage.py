"""The ``publish`` stage, against a fake Hub.

No test here constructs the real Hub, so none can reach the network or a credential. The property
that matters most — **a dry run writes nothing** — is asserted on the fake's own call log rather
than inferred from reading the code.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from finepdf_to_images.adapters.hub import FakeHub, HubError
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DOCUMENTS_FILE,
    DOCUMENTS_RELEVANT_FILE,
    DOCUMENTS_RETRIEVED_FILE,
    IMAGES_FILE,
    MANIFEST_FILE,
    PublicationError,
)
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.serialization import sha256_hex
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
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
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
    assert set(hub.files) == {
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
        IMAGES_FILE,
    }
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
    assert set(hub.files) == {
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
        IMAGES_FILE,
    }
    assert hub.files[DOCUMENTS_FILE] == b""
    assert hub.files[DOCUMENTS_RELEVANT_FILE] == b""
    assert hub.files[DOCUMENTS_RETRIEVED_FILE] == b""


# --------------------------------------------------------------------------- published content


def test_the_published_rows_are_canonical_jsonl() -> None:
    hub = FakeHub()
    publish(hub, apply=True)
    for path, expected_count in [
        (DOCUMENTS_FILE, 2),
        (DOCUMENTS_RELEVANT_FILE, 1),
        (DOCUMENTS_RETRIEVED_FILE, 1),
    ]:
        lines = hub.files[path].decode().splitlines()
        assert len(lines) == expected_count
        for line in lines:
            row = json.loads(line)
            assert list(row) == sorted(row), f"canonical JSON sorts its keys: {path}"


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
        def upload(self, plan, message, delete=()):  # type: ignore[no-untyped-def]
            revision = super().upload(plan, message, delete)
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
    assert set(result.missing) == {
        CARD_FILE,
        MANIFEST_FILE,
        DOCUMENTS_FILE,
        DOCUMENTS_RELEVANT_FILE,
        DOCUMENTS_RETRIEVED_FILE,
        IMAGES_FILE,
    }


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


# --------------------------------------------------------------------------- artifact bytes
#
# The loading path is the one that reads third-party bytes off disk and hands them to an upload.
# It was the least covered code in the project when the CRAP gate first saw it, which is how a
# publication path usually goes wrong: everything around it is tested and it is not.

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


def _cleared_run(tmp_path: pathlib.Path, *, pdf: bytes, image: bytes) -> dict[str, Any]:
    """A one-document run whose artifact bytes are on disk where the stage expects them."""
    retrieval = {**retrieved_row(0), "publication": CLEARED_PUBLICATION}
    retrieval["sha256"] = sha256_hex(pdf)
    document = {**document_row(0), "pdf_sha256": sha256_hex(pdf)}
    picture = {
        **image_row(0),
        "sha256": sha256_hex(image),
        "pdf_sha256": sha256_hex(pdf),
    }

    pdf_root = tmp_path / "retrieve"
    image_root = tmp_path / "extract"
    pdf_file = pdf_root / artifact_path(sha256_hex(pdf))
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    pdf_file.write_bytes(pdf)
    image_file = image_root / image_path(sha256_hex(image), "image/png")
    image_file.parent.mkdir(parents=True, exist_ok=True)
    image_file.write_bytes(image)

    return {
        "scored": [scored_row(0, relevant=True)],
        "retrieved": [retrieval],
        "documents": [document],
        "images": [picture],
        "pdf_root": pdf_root,
        "image_root": image_root,
    }


def _publish_cleared(hub: FakeHub, run: dict[str, Any], **overrides: Any) -> PublicationResult:
    return run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        scored=run["scored"],
        retrieved=run["retrieved"],
        documents=run["documents"],
        images=run["images"],
        extract_manifest=EXTRACT_MANIFEST,
        pdf_root=run["pdf_root"],
        image_root=run["image_root"],
        **overrides,
    )


def test_a_cleared_run_publishes_the_artifact_bytes(tmp_path: pathlib.Path) -> None:
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    hub = FakeHub()
    result = _publish_cleared(hub, run, apply=True)

    published = {file.path: file.data for file in result.plan.files}
    assert published[artifact_path(sha256_hex(b"%PDF-1.4 cleared"))] == b"%PDF-1.4 cleared"
    assert image_path(sha256_hex(b"\x89PNG\r\n\x1a\npixels"), "image/png") in published
    assert not result.missing


def test_the_published_row_points_at_the_bytes_that_shipped(tmp_path: pathlib.Path) -> None:
    """The index and the payload are one statement, so the row's path must name a real file."""
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    result = _publish_cleared(FakeHub(), run)

    files = {file.path for file in result.plan.files}
    rows = [
        json.loads(line)
        for file in result.plan.files
        if file.path == "data/images.jsonl"
        for line in file.data.decode().splitlines()
    ]
    assert rows and all(row["image"] in files for row in rows)


def test_bytes_that_disagree_with_the_index_are_refused(tmp_path: pathlib.Path) -> None:
    """A truncated write or an edited working copy must not publish under the indexed digest."""
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    (run["pdf_root"] / artifact_path(sha256_hex(b"%PDF-1.4 cleared"))).write_bytes(b"%PDF- other")

    with pytest.raises(PublicationError, match="refusing to publish bytes under a digest"):
        _publish_cleared(FakeHub(), run)


def test_omitting_a_root_is_refused_rather_than_silently_publishing_less(
    tmp_path: pathlib.Path,
) -> None:
    """REGRESSION: a forgotten root used to shrink the plan, which now *deletes* published bytes.

    Dropping artifacts from the plan was survivable while publication could only add files -- the
    bytes just were not uploaded that run. Once a publication also removes what it does not
    contain, the same forgotten flag deletes already-published bytes from a public dataset, with
    exit code 0 and no warning.
    """
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    with pytest.raises(PublicationError, match="--pdf-root was not given"):
        run_publish(
            hub=FakeHub(),
            repo="NoeFlandre/finepdf-to-images-poc",
            select_manifest=SELECT_MANIFEST,
            scored=run["scored"],
            retrieved=run["retrieved"],
            documents=run["documents"],
            images=run["images"],
            extract_manifest=EXTRACT_MANIFEST,
        )


def test_a_missing_artifact_file_fails_loudly(tmp_path: pathlib.Path) -> None:
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    (run["pdf_root"] / artifact_path(sha256_hex(b"%PDF-1.4 cleared"))).unlink()
    with pytest.raises(OSError):
        _publish_cleared(FakeHub(), run)


def test_publishing_cleared_bytes_twice_is_still_a_no_op(tmp_path: pathlib.Path) -> None:
    run = _cleared_run(tmp_path, pdf=b"%PDF-1.4 cleared", image=b"\x89PNG\r\n\x1a\npixels")
    hub = FakeHub()
    _publish_cleared(hub, run, apply=True)
    commits = len(hub.commits)
    second = _publish_cleared(hub, run, apply=True)
    assert second.noop
    assert len(hub.commits) == commits, "artifacts must not re-upload on every run"


# --------------------------------------------------------------------------- cleanup


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
