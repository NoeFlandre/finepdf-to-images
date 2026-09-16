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
