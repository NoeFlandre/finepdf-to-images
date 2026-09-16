"""The pinned, bounded input contract."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.serialization import canonical_bytes
from finepdf_to_images.domain.source import (
    DEFAULT_LIMIT,
    DEFAULT_REVISION,
    SamplingSpec,
    SourceConfigurationError,
    SourceRecord,
    SourceRef,
    build_manifest,
    build_record,
    read_window,
    select,
)


def make_record(index: int, row_id: str | None = None) -> SourceRecord:
    return SourceRecord(
        row_index=index,
        row_id=row_id or f"<urn:uuid:{index:08d}>",
        url=f"https://example.invalid/{index}.pdf",
        date="2023-01-30T22:07:32+00:00",
        dump="CC-MAIN-2023-06",
        language="eng_Latn",
        full_doc_lid="eng_Latn",
        full_doc_lid_score=0.5,
        token_count=376,
        is_truncated=False,
        extractor="docling",
        text=f"document {index}",
    )


# --------------------------------------------------------------------------- SourceRef


def test_default_ref_points_at_the_documented_pilot_shard() -> None:
    ref = SourceRef()
    assert ref.dataset == "HuggingFaceFW/finepdfs"
    assert ref.revision == DEFAULT_REVISION
    assert ref.path == "data/eng_Latn/train/000_00000.parquet"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "main"),
        ("revision", "220BAC3ACBF07789502C621D2D33952F51AC7F86"),
        ("revision", ""),
        ("config", "english"),
        ("config", "../../etc"),
        ("config", "eng_latn"),
        ("split", "Train"),
        ("split", "train/../.."),
        ("shard", "0000.parquet"),
        ("shard", "000_00000.parquet.bak"),
        ("shard", "*"),
    ],
)
def test_invalid_reference_is_refused_before_any_read(field: str, value: str) -> None:
    with pytest.raises(SourceConfigurationError) as excinfo:
        SourceRef(**{field: value})
    assert field in str(excinfo.value)


def test_a_different_shard_changes_the_source_identity() -> None:
    assert SourceRef().as_dict() != SourceRef(shard="000_00001.parquet").as_dict()


# --------------------------------------------------------------------------- SamplingSpec


def test_sampling_defaults_are_small() -> None:
    assert SamplingSpec().limit == DEFAULT_LIMIT == 100


@pytest.mark.parametrize("limit", [0, -1, -100, True, 1.5, "100"])
def test_non_positive_or_non_integer_limit_is_refused(limit: object) -> None:
    with pytest.raises(SourceConfigurationError):
        SamplingSpec(limit=limit)  # ty: ignore[invalid-argument-type]


def test_unknown_strategy_is_refused() -> None:
    with pytest.raises(SourceConfigurationError):
        SamplingSpec(strategy="random")


def test_empty_seed_is_refused() -> None:
    with pytest.raises(SourceConfigurationError):
        SamplingSpec(seed="")


# --------------------------------------------------------------------------- read window


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(1, 1000), (100, 1000), (1000, 1000), (1001, 2000), (2000, 2000)],
)
def test_read_window_rounds_up_to_whole_row_groups(limit: int, expected: int) -> None:
    assert read_window(limit) == expected


def test_read_window_rejects_a_non_positive_row_group_size() -> None:
    with pytest.raises(SourceConfigurationError):
        read_window(10, rows_per_row_group=0)


# --------------------------------------------------------------------------- selection


def test_head_strategy_keeps_the_first_rows_in_shard_order() -> None:
    selected = select([make_record(i) for i in range(10)], SamplingSpec(limit=3))
    assert [record.row_index for record in selected] == [0, 1, 2]


def test_selection_never_exceeds_the_limit() -> None:
    assert len(select([make_record(i) for i in range(500)], SamplingSpec(limit=100))) == 100


def test_selection_of_fewer_rows_than_the_limit_returns_them_all() -> None:
    assert len(select([make_record(i) for i in range(4)], SamplingSpec(limit=100))) == 4


def test_selection_of_no_rows_is_empty_not_an_error() -> None:
    assert select([], SamplingSpec()) == []


def test_hash_strategy_is_stable_across_input_ordering() -> None:
    records = [make_record(i) for i in range(50)]
    spec = SamplingSpec(limit=7, strategy="hash")
    assert select(records, spec) == select(list(reversed(records)), spec)


def test_hash_strategy_returns_results_in_shard_order() -> None:
    selected = select([make_record(i) for i in range(50)], SamplingSpec(limit=7, strategy="hash"))
    indices = [record.row_index for record in selected]
    assert indices == sorted(indices)


def test_hash_strategy_depends_on_the_seed() -> None:
    records = [make_record(i) for i in range(200)]
    a = select(records, SamplingSpec(limit=10, strategy="hash", seed="seed-a"))
    b = select(records, SamplingSpec(limit=10, strategy="hash", seed="seed-b"))
    assert a != b


def test_hash_strategy_replays_identically_from_the_same_seed() -> None:
    records = [make_record(i) for i in range(200)]
    spec = SamplingSpec(limit=10, strategy="hash", seed="replay")
    assert select(records, spec) == select(records, spec)


@given(
    count=st.integers(min_value=0, max_value=60),
    limit=st.integers(min_value=1, max_value=60),
    strategy=st.sampled_from(["head", "hash"]),
)
def test_selection_is_bounded_ordered_and_a_subset(count: int, limit: int, strategy: str) -> None:
    records = [make_record(i) for i in range(count)]
    selected = select(records, SamplingSpec(limit=limit, strategy=strategy))
    indices = [record.row_index for record in selected]
    assert len(selected) == min(count, limit)
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)
    assert set(selected) <= set(records)


# --------------------------------------------------------------------------- row projection


def test_build_record_projects_the_used_columns() -> None:
    record = build_record(
        3,
        {
            "id": "<urn:uuid:x>",
            "url": "https://example.invalid/a.pdf",
            "date": "2023-01-30T22:07:32+00:00",
            "dump": "CC-MAIN-2023-06",
            "language": "eng_Latn",
            "full_doc_lid": "eng_Latn",
            "full_doc_lid_score": 0.42,
            "token_count": 376,
            "is_truncated": False,
            "extractor": "docling",
            "text": "hello",
        },
    )
    assert record.row_index == 3
    assert record.row_id == "<urn:uuid:x>"
    assert record.text == "hello"


def test_build_record_tolerates_missing_optional_metadata() -> None:
    record = build_record(0, {"id": "a", "url": "https://example.invalid/a.pdf"})
    assert record.token_count == 0
    assert record.full_doc_lid_score == 0.0
    assert record.text == ""


@pytest.mark.parametrize(
    "row",
    [
        {"url": "https://example.invalid/a.pdf"},
        {"id": "a"},
        {"id": "", "url": "https://example.invalid/a.pdf"},
        {"id": "a", "url": ""},
        {"id": None, "url": None},
    ],
)
def test_untraceable_row_is_refused(row: dict[str, object]) -> None:
    with pytest.raises(SourceConfigurationError):
        build_record(0, row)


def test_without_text_drops_the_body_but_keeps_provenance() -> None:
    reduced = make_record(1).without_text()
    assert "text" not in reduced
    assert reduced["row_id"] and reduced["url"] and reduced["row_index"] == 1


# --------------------------------------------------------------------------- manifest


def manifest_for(
    records: list[SourceRecord],
    ref: SourceRef | None = None,
    spec: SamplingSpec | None = None,
) -> dict[str, Any]:
    return build_manifest(
        ref=ref or SourceRef(),
        spec=spec or SamplingSpec(),
        records=records,
        rows_read=1000,
        rows_per_row_group=1000,
    )


def test_manifest_records_full_source_provenance() -> None:
    manifest = manifest_for([make_record(0)])
    assert manifest["source"] == SourceRef().as_dict()
    assert manifest["sampling"] == SamplingSpec().as_dict()
    assert manifest["counts"] == {"selected": 1}


def test_manifest_excludes_document_bodies() -> None:
    manifest = manifest_for([make_record(0)])
    assert "text" not in manifest["records"][0]


def test_manifest_bytes_are_identical_across_runs() -> None:
    records = [make_record(i) for i in range(5)]
    assert canonical_bytes(manifest_for(records)) == canonical_bytes(manifest_for(records))


def test_manifest_identity_changes_with_the_source() -> None:
    records = [make_record(0)]
    other = manifest_for(records, ref=SourceRef(shard="000_00001.parquet"))
    assert canonical_bytes(manifest_for(records)) != canonical_bytes(other)


def test_manifest_identity_changes_with_the_sampling_parameters() -> None:
    records = [make_record(0)]
    other = manifest_for(records, spec=SamplingSpec(limit=1, seed="other", strategy="hash"))
    assert canonical_bytes(manifest_for(records)) != canonical_bytes(other)


def test_records_digest_covers_the_selected_rows() -> None:
    a = manifest_for([make_record(0)])
    b = manifest_for([make_record(1)])
    assert a["records_digest"] != b["records_digest"]
