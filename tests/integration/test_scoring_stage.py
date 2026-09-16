"""The ``score`` stage: select's output in, a scored manifest out. Offline and deterministic."""

from __future__ import annotations

import pathlib

import pytest

from finepdf_to_images.adapters.source import LocalShardReader
from finepdf_to_images.adapters.storage import read_jsonl
from finepdf_to_images.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main
from finepdf_to_images.domain.source import SamplingSpec, SourceRef
from finepdf_to_images.pipeline import run_score, run_select

pytestmark = pytest.mark.integration

FIXTURE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "shards"


@pytest.fixture
def selected(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real ``select`` output to score, so the two stages are tested as they compose."""
    result = run_select(
        reader=LocalShardReader(root=FIXTURE_ROOT),
        ref=SourceRef(),
        spec=SamplingSpec(limit=20),
        out_dir=tmp_path / "select",
    )
    return result.records_path


def test_scoring_select_output_separates_the_agriculture_rows(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The fixture makes every third row agricultural, so the counts are known in advance."""
    result = run_score(records=read_jsonl(selected), out_dir=tmp_path / "score")
    assert result.scored == 20
    assert result.relevant == 7  # rows 0, 3, 6, 9, 12, 15, 18
    assert 0 < result.relevant < result.scored


def test_every_relevant_row_carries_its_evidence(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    result = run_score(records=read_jsonl(selected), out_dir=tmp_path / "score")
    for row in read_jsonl(result.scored_path):
        relevance = row["relevance"]
        assert bool(relevance["evidence"]) == (relevance["score"] > 0)
        if relevance["relevant"]:
            assert relevance["evidence"], f"row {row['row_index']} is relevant with no evidence"


def test_scored_rows_keep_the_link_back_to_the_source_row(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    result = run_score(records=read_jsonl(selected), out_dir=tmp_path / "score")
    for row in read_jsonl(result.scored_path):
        assert row["row_id"].startswith("<urn:uuid:")
        assert isinstance(row["row_index"], int)
        assert row["url"]


def test_the_empty_body_row_is_a_valid_negative(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The fixture's row 7 has whitespace-only text; it must score, not crash."""
    result = run_score(records=read_jsonl(selected), out_dir=tmp_path / "score")
    row = next(r for r in read_jsonl(result.scored_path) if r["row_index"] == 7)
    assert row["relevance"]["relevant"] is False
    assert row["relevance"]["score"] == 0


def test_scoring_is_byte_identical_across_runs(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    records = read_jsonl(selected)
    first = run_score(records=records, out_dir=tmp_path / "a")
    second = run_score(records=records, out_dir=tmp_path / "b")
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.scored_path.read_bytes() == second.scored_path.read_bytes()


def test_the_manifest_records_the_vocabulary_that_produced_the_decisions(
    selected: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Without this, a threshold change silently reinterprets an old run's output."""
    manifest = run_score(records=read_jsonl(selected), out_dir=tmp_path / "score").manifest
    vocabulary = manifest["vocabulary"]
    assert vocabulary["language"] == "eng_Latn"
    assert vocabulary["term_count"] > 0
    assert vocabulary["thresholds"]["min_groups"] >= 1
    assert vocabulary["excluded_ambiguous_terms"]


def test_scoring_no_records_is_an_empty_run_not_an_error(tmp_path: pathlib.Path) -> None:
    result = run_score(records=[], out_dir=tmp_path)
    assert result.scored == 0
    assert result.relevant == 0
    assert result.scored_path.read_bytes() == b""


def test_scoring_a_record_with_no_text_field_is_a_negative(tmp_path: pathlib.Path) -> None:
    result = run_score(records=[{"row_index": 0, "row_id": "a", "url": "u"}], out_dir=tmp_path)
    assert result.relevant == 0


# --------------------------------------------------------------------------- CLI


def run_cli(args: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def test_cli_score_runs_against_select_output(
    selected: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(["score", "--records", str(selected), "--out", str(tmp_path / "s")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "scored     20" in out
    assert "relevant   7" in out


def test_cli_score_requires_its_arguments() -> None:
    assert run_cli(["score"]) == EXIT_USAGE


def test_cli_score_reports_a_missing_records_file_as_a_failure(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(["score", "--records", str(tmp_path / "nope.jsonl"), "--out", str(tmp_path)])
    assert code == EXIT_FAILURE
    assert "no such file" in capsys.readouterr().err
