"""Canonical serialization is the foundation every determinism claim rests on."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.serialization import (
    canonical_bytes,
    canonical_json,
    canonical_jsonl,
    content_digest,
    sha256_hex,
)

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4)
    ),
    max_leaves=12,
)


def test_key_order_does_not_change_the_bytes() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})


def test_output_has_no_incidental_whitespace() -> None:
    assert canonical_json({"a": [1, 2]}) == '{"a":[1,2]}'


def test_unicode_is_not_escaped() -> None:
    assert canonical_json({"crop": "blé"}) == '{"crop":"blé"}'


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_refused(value: float) -> None:
    """They are not valid JSON; emitting them would produce a file nothing can read back."""
    with pytest.raises(ValueError):
        canonical_json({"score": value})


def test_jsonl_is_one_document_per_line_with_a_trailing_newline() -> None:
    assert canonical_jsonl([{"a": 1}, {"b": 2}]) == b'{"a":1}\n{"b":2}\n'


def test_empty_jsonl_is_empty_not_a_blank_line() -> None:
    assert canonical_jsonl([]) == b""


def test_sha256_matches_the_known_digest_of_the_empty_string() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_digest_is_the_hash_of_the_canonical_bytes() -> None:
    value = {"b": [1, 2], "a": "x"}
    assert content_digest(value) == sha256_hex(canonical_bytes(value))


@pytest.mark.property
@given(value=json_values)
def test_canonical_form_round_trips(value: object) -> None:
    assert json.loads(canonical_json(value)) == value


@pytest.mark.property
@given(value=json_values)
def test_serialization_is_idempotent(value: object) -> None:
    assert canonical_bytes(value) == canonical_bytes(json.loads(canonical_json(value)))


@pytest.mark.property
@given(value=json_values)
def test_equal_values_hash_equal(value: object) -> None:
    assert content_digest(value) == content_digest(json.loads(canonical_json(value)))
