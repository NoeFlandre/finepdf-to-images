"""Bootstrap CLI contract: stable exit codes and a documented help path."""

from __future__ import annotations

import subprocess
import sys

import pytest

from finepdf_to_images import __version__
from finepdf_to_images.cli import EXIT_OK, EXIT_USAGE, main


def test_version_is_declared() -> None:
    assert __version__.count(".") == 2


@pytest.mark.parametrize("argv", [["--help"], ["version"]])
def test_documented_paths_exit_ok(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == EXIT_OK
    assert capsys.readouterr().out.strip()


def test_unknown_command_exits_usage() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["definitely-not-a-command"])
    assert excinfo.value.code == EXIT_USAGE


def test_no_arguments_exits_usage() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == EXIT_USAGE


def test_console_script_module_entry_point_runs() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "finepdf_to_images", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == EXIT_OK
    assert "finepdf-to-images" in completed.stdout


# --------------------------------------------------------------------------- removal reporting


def test_a_publication_with_nothing_stale_reports_no_removals(capsys) -> None:  # type: ignore[no-untyped-def]
    from finepdf_to_images.cli import _report_removals

    _report_removals(())
    assert capsys.readouterr().out == "", "a clean run must not print an empty removal block"


def test_every_stale_path_is_named_while_the_list_is_short(capsys) -> None:  # type: ignore[no-untyped-def]
    from finepdf_to_images.cli import _report_removals

    _report_removals(("data/old.jsonl", "manifest.json"))
    out = capsys.readouterr().out
    assert "remove     2 published file(s)" in out
    assert "data/old.jsonl" in out
    assert "manifest.json" in out
    assert "more" not in out


def test_a_long_removal_list_is_truncated_but_still_reports_the_true_total(capsys) -> None:  # type: ignore[no-untyped-def]
    """The first cleanup of the old layout removes ~200 files.

    A reviewable dry run names enough to recognise what is going and states the real count; it
    does not print two hundred paths, and it must never understate the total.
    """
    from finepdf_to_images.cli import _report_removals

    _report_removals(tuple(f"images/ab/cd/{index:03d}.png" for index in range(198)))
    out = capsys.readouterr().out
    assert "remove     198 published file(s)" in out
    assert "... and 188 more" in out
    assert out.count("images/ab/cd/") == 10
