"""Reading a bounded window of a shard, and the ``select`` command end to end.

Deterministic and offline: everything runs against the committed synthetic shard fixture. No
network, no Hugging Face token.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from finepdf_to_images.adapters.source import LocalShardReader, ShardWindow
from finepdf_to_images.cli import EXIT_OK, EXIT_USAGE, main
from finepdf_to_images.domain.source import (
    SamplingSpec,
    SourceConfigurationError,
    SourceRef,
)
from finepdf_to_images.pipeline import run_select
from tests.fixtures.build_shard_fixture import ROW_COUNT, ROWS_PER_ROW_GROUP

pytestmark = pytest.mark.integration

FIXTURE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "shards"


@pytest.fixture
def reader() -> LocalShardReader:
    return LocalShardReader(root=FIXTURE_ROOT)


# --------------------------------------------------------------------------- bounded reading


def test_reader_returns_the_shard_shape(reader: LocalShardReader) -> None:
    window = reader.read(SourceRef(), max_rows=ROW_COUNT)
    assert window.total_rows == ROW_COUNT
    assert window.rows_per_row_group == ROWS_PER_ROW_GROUP
    assert window.total_row_groups == ROW_COUNT // ROWS_PER_ROW_GROUP


@pytest.mark.parametrize("max_rows", [1, 3, 5, 6, 20])
def test_reader_never_returns_more_than_requested(reader: LocalShardReader, max_rows: int) -> None:
    assert len(reader.read(SourceRef(), max_rows=max_rows).rows) == min(max_rows, ROW_COUNT)


def test_reader_stops_after_the_row_groups_the_limit_requires(reader: LocalShardReader) -> None:
    """The whole point: a small limit must not pull the rest of a multi-gigabyte shard."""
    window = reader.read(SourceRef(), max_rows=ROWS_PER_ROW_GROUP)
    assert window.total_rows == ROW_COUNT
    assert len(window.rows) == ROWS_PER_ROW_GROUP


def test_reader_requesting_more_rows_than_exist_returns_what_there_is(
    reader: LocalShardReader,
) -> None:
    assert len(reader.read(SourceRef(), max_rows=10_000).rows) == ROW_COUNT


def test_reader_reads_only_the_selected_columns(reader: LocalShardReader) -> None:
    window = reader.read(SourceRef(), max_rows=1, columns=("id", "url"))
    assert set(window.rows[0]) == {"id", "url"}


def test_reader_refuses_a_column_the_shard_does_not_have(reader: LocalShardReader) -> None:
    with pytest.raises(SourceConfigurationError, match="missing expected columns"):
        reader.read(SourceRef(), max_rows=1, columns=("id", "not_a_column"))


def test_reader_refuses_a_non_positive_limit(reader: LocalShardReader) -> None:
    with pytest.raises(SourceConfigurationError):
        reader.read(SourceRef(), max_rows=0)


def test_missing_local_shard_fails_without_falling_back(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SourceConfigurationError, match="Refusing to fall back"):
        LocalShardReader(root=tmp_path).read(SourceRef(), max_rows=1)


def test_a_different_shard_name_is_not_silently_substituted(reader: LocalShardReader) -> None:
    with pytest.raises(SourceConfigurationError):
        reader.read(SourceRef(shard="000_00001.parquet"), max_rows=1)


# --------------------------------------------------------------------------- pipeline


def test_selection_writes_a_manifest_and_records(
    reader: LocalShardReader, tmp_path: pathlib.Path
) -> None:
    result = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=3), out_dir=tmp_path
    )
    assert result.selected == 3
    assert result.manifest_path.is_file()
    assert result.records_path.read_bytes().count(b"\n") == 3


def test_selection_is_byte_identical_across_runs(
    reader: LocalShardReader, tmp_path: pathlib.Path
) -> None:
    """The headline determinism claim for this stage."""
    first = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=5), out_dir=tmp_path / "a"
    )
    second = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=5), out_dir=tmp_path / "b"
    )
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.records_path.read_bytes() == second.records_path.read_bytes()


def test_selection_honours_the_limit(reader: LocalShardReader, tmp_path: pathlib.Path) -> None:
    result = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=4), out_dir=tmp_path
    )
    assert result.selected == 4
    assert result.manifest["counts"] == {"selected": 4}


def test_changing_the_sampling_changes_the_output_identity(
    reader: LocalShardReader, tmp_path: pathlib.Path
) -> None:
    head = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=5), out_dir=tmp_path / "head"
    )
    hashed = run_select(
        reader=reader,
        ref=SourceRef(),
        spec=SamplingSpec(limit=5, strategy="hash"),
        out_dir=tmp_path / "hash",
    )
    assert head.manifest["records_digest"] != hashed.manifest["records_digest"]


def test_manifest_preserves_source_provenance(
    reader: LocalShardReader, tmp_path: pathlib.Path
) -> None:
    manifest = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=1), out_dir=tmp_path
    ).manifest
    assert manifest["source"] == SourceRef().as_dict()
    row = manifest["records"][0]
    assert row["row_id"].startswith("<urn:uuid:")
    assert row["row_index"] == 0


def test_records_file_keeps_the_document_text(
    reader: LocalShardReader, tmp_path: pathlib.Path
) -> None:
    result = run_select(
        reader=reader, ref=SourceRef(), spec=SamplingSpec(limit=1), out_dir=tmp_path
    )
    assert b'"text":' in result.records_path.read_bytes()


def test_reader_protocol_accepts_a_stub() -> None:
    """The adapter is injectable: a fixture object satisfies the port without pyarrow."""

    class StubReader:
        def read(self, ref: SourceRef, *, max_rows: int, columns: object = ()) -> ShardWindow:
            return ShardWindow(
                rows=[{"id": "a", "url": "https://fixtures.invalid/a.pdf"}],
                rows_per_row_group=1,
                total_rows=1,
                total_row_groups=1,
            )

    result = run_select(
        reader=StubReader(),
        ref=SourceRef(),
        spec=SamplingSpec(limit=1),
        out_dir=pathlib.Path(__file__).parent / "__unused__",
    )
    assert result.selected == 1
    result.manifest_path.parent.joinpath("manifest.json").unlink()
    result.records_path.unlink()
    result.manifest_path.parent.rmdir()


# --------------------------------------------------------------------------- CLI


def run_cli(args: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def test_cli_select_runs_offline_against_the_fixture(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(
        ["select", "--source-dir", str(FIXTURE_ROOT), "--limit", "3", "--out", str(tmp_path)]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "selected   3" in out
    assert "HuggingFaceFW/finepdfs@220bac3acbf07789502c621d2d33952f51ac7f86" in out
    assert (tmp_path / "manifest.json").is_file()


@pytest.mark.parametrize(
    "extra",
    [
        ["--config", "english"],
        ["--split", "Train"],
        ["--shard", "0000.parquet"],
        ["--revision", "main"],
        ["--limit", "0"],
    ],
)
def test_cli_rejects_an_invalid_reference_with_a_usage_error(
    tmp_path: pathlib.Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(["select", "--source-dir", str(FIXTURE_ROOT), "--out", str(tmp_path), *extra])
    assert code == EXIT_USAGE
    assert not (tmp_path / "manifest.json").exists()


def test_cli_rejects_an_unknown_strategy(tmp_path: pathlib.Path) -> None:
    assert (
        run_cli(
            [
                "select",
                "--source-dir",
                str(FIXTURE_ROOT),
                "--strategy",
                "random",
                "--out",
                str(tmp_path),
            ]
        )
        == EXIT_USAGE
    )


def test_cli_requires_an_output_directory() -> None:
    assert run_cli(["select", "--source-dir", str(FIXTURE_ROOT)]) == EXIT_USAGE


def test_cli_select_is_byte_identical_when_re_run(tmp_path: pathlib.Path) -> None:
    """Runs the real console script twice in a subprocess, which is what the smoke path does."""
    for name in ("a", "b"):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "finepdf_to_images",
                "select",
                "--source-dir",
                str(FIXTURE_ROOT),
                "--limit",
                "5",
                "--out",
                str(tmp_path / name),
            ],
            capture_output=True,
            check=False,
        )
        assert completed.returncode == EXIT_OK, completed.stderr.decode()
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (
        tmp_path / "b" / "manifest.json"
    ).read_bytes()
