"""Agriculture relevance scoring."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.scoring import (
    EXCLUDED_AMBIGUOUS_TERMS,
    MIN_GROUPS,
    MIN_TERMS_IN_ONE_GROUP,
    VOCABULARY,
    VOCABULARY_LANGUAGE,
    normalize,
    score,
    vocabulary_summary,
)
from finepdf_to_images.domain.serialization import canonical_bytes

AGRICULTURE = (
    "Irrigation scheduling for maize and wheat under drought stress. Soil moisture and "
    "fertilizer application were measured across the farm."
)
UNRELATED = (
    "Quarterly municipal council agenda. Call to order, land acknowledgement, attendance, "
    "approval of minutes, and the treasurer's report on parking revenue."
)


# --------------------------------------------------------------------------- the headline cases


def test_agriculture_text_is_relevant_with_evidence() -> None:
    result = score(AGRICULTURE)
    assert result.relevant
    assert result.score >= MIN_GROUPS
    assert "irrigation" in result.evidence["irrigation"]
    assert "maize" in result.evidence["crops"]
    assert "soil moisture" in result.evidence["soil"]


def test_unrelated_text_is_not_relevant() -> None:
    result = score(UNRELATED)
    assert not result.relevant
    assert result.matched_groups == ()
    assert result.evidence == {}


@pytest.mark.parametrize(
    # Escaped rather than literal so the source file itself stays free of ambiguous characters:
    # em dash, en dash, ellipsis, NUL.
    "text",
    ["", "   ", "\n\t  \n", "...", "\u2014 \u2013 \u2026", "\x00"],
)
def test_empty_or_punctuation_only_text_is_a_valid_negative(text: str) -> None:
    """Not an exception: "this is not about agriculture" is an ordinary answer."""
    result = score(text)
    assert not result.relevant
    assert result.score == 0


# --------------------------------------------------------------------------- what must NOT match


@pytest.mark.parametrize(
    "text",
    [
        "Please crop the image and adjust the field of view.",
        "The bull market rewarded seed investors; yields on bonds fell.",
        "We harvest telemetry from the plant floor into a data field.",
        "Company culture is the seed of long-term yield.",
    ],
)
def test_ambiguous_words_alone_do_not_make_a_document_agricultural(text: str) -> None:
    """crop, yield, field, plant, harvest, bull, seed and culture are commoner outside farming."""
    assert not score(text).relevant


@pytest.mark.parametrize("term", sorted(EXCLUDED_AMBIGUOUS_TERMS))
def test_the_excluded_terms_really_are_absent_from_the_vocabulary(term: str) -> None:
    assert not any(term in terms for terms in VOCABULARY.values())


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("wheaten bread and cowardly acts", "wheat"),
        ("a cowardly farmer-adjacent anecdote", "cow"),
        ("the goatee and the barleycorn", "goat"),
        ("irrigational studies", "irrigation"),
    ],
)
def test_terms_match_whole_words_not_substrings(text: str, term: str) -> None:
    """Substring matching would make "cow" match "coward" and "wheat" match "wheaten"."""
    assert term not in {t for terms in score(text).evidence.values() for t in terms}


# --------------------------------------------------------------------------- the thresholds


def test_a_single_incidental_group_is_not_enough() -> None:
    result = score("The vineyard was mentioned once in passing.")
    assert result.matched_groups == ("crops",)
    assert not result.relevant


def test_two_groups_are_enough() -> None:
    result = score("The farm uses drip irrigation.")
    assert len(result.matched_groups) >= MIN_GROUPS
    assert result.relevant


def test_enough_depth_in_one_group_is_enough() -> None:
    """A narrowly focused document -- a wheat agronomy paper -- that breadth alone would miss."""
    result = score("Wheat, barley and sorghum cultivar trials with staggered sowing.")
    assert result.matched_groups == ("crops",)
    assert len(result.evidence["crops"]) >= MIN_TERMS_IN_ONE_GROUP
    assert result.relevant


# --------------------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "text",
    [
        "IRRIGATION of MAIZE",
        "irrigation, maize.",
        "  irrigation\n\nmaize  ",
        "irrigation---maize",
        "(irrigation) [maize]",
        # Full-width Latin: "IRRIGATION MAIZE", which NFKC folds onto the plain letters.
        "\uff29\uff32\uff32\uff29\uff27\uff21\uff34\uff29\uff2f\uff2e"
        " \uff2d\uff21\uff29\uff3a\uff25",
    ],
)
def test_case_punctuation_and_typographic_variants_do_not_change_the_result(text: str) -> None:
    result = score(text)
    assert result.matched_groups == ("crops", "irrigation")


def test_repeating_a_term_does_not_inflate_the_score() -> None:
    once = score("irrigation maize")
    many = score("irrigation maize " * 40)
    assert once.score == many.score
    assert once.evidence == many.evidence


def test_normalize_is_idempotent() -> None:
    assert normalize(normalize("Maize, Wheat!")) == normalize("Maize, Wheat!")


def test_a_phrase_matches_across_punctuation_but_not_across_a_gap() -> None:
    assert "soil moisture" in score("soil, moisture").evidence.get("soil", ())
    assert "soil moisture" not in score("soil in the moisture").evidence.get("soil", ())


# --------------------------------------------------------------------------- language honesty


def test_the_result_records_the_language_it_was_told() -> None:
    result = score(AGRICULTURE, language="por_Latn")
    assert result.language == "por_Latn"
    assert result.vocabulary_language == VOCABULARY_LANGUAGE
    assert not result.language_matches_vocabulary


def test_the_default_language_matches_the_vocabulary() -> None:
    assert score(AGRICULTURE).language_matches_vocabulary


def test_language_does_not_secretly_change_the_decision() -> None:
    """The scorer does not pretend to be multilingual; it records the mismatch and scores anyway."""
    assert score(AGRICULTURE, language="por_Latn").relevant == score(AGRICULTURE).relevant


# --------------------------------------------------------------------------- determinism


def test_repeated_scoring_is_byte_identical() -> None:
    assert canonical_bytes(score(AGRICULTURE).as_dict()) == canonical_bytes(
        score(AGRICULTURE).as_dict()
    )


def test_evidence_is_sorted_so_the_output_is_stable() -> None:
    result = score("sorghum, barley, maize, wheat and drip irrigation")
    assert list(result.matched_groups) == sorted(result.matched_groups)
    for terms in result.evidence.values():
        assert list(terms) == sorted(terms)


def test_the_summary_documents_the_vocabulary_and_thresholds() -> None:
    summary = vocabulary_summary()
    assert summary["language"] == VOCABULARY_LANGUAGE
    assert summary["thresholds"] == {
        "min_groups": MIN_GROUPS,
        "min_terms_in_one_group": MIN_TERMS_IN_ONE_GROUP,
    }
    assert set(summary["groups"]) == set(VOCABULARY)
    assert summary["excluded_ambiguous_terms"] == sorted(EXCLUDED_AMBIGUOUS_TERMS)


# --------------------------------------------------------------------------- properties


@given(text=st.text(max_size=400))
def test_scoring_arbitrary_unicode_never_raises(text: str) -> None:
    result = score(text)
    assert isinstance(result.relevant, bool)
    assert result.score >= 0


@given(text=st.text(max_size=200))
def test_scoring_is_deterministic_for_any_input(text: str) -> None:
    assert score(text).as_dict() == score(text).as_dict()


@given(
    text=st.text(max_size=200),
    pad=st.sampled_from(["", " ", "\n", "\t", "   \n\t "]),
)
def test_surrounding_whitespace_never_changes_the_decision(text: str, pad: str) -> None:
    assert score(f"{pad}{text}{pad}").relevant == score(text).relevant


@given(text=st.text(max_size=200))
def test_the_score_never_exceeds_the_number_of_groups(text: str) -> None:
    result = score(text)
    assert result.score == len(result.matched_groups) <= len(VOCABULARY)
    assert set(result.matched_groups) == set(result.evidence)


@given(text=st.text(max_size=200))
def test_every_piece_of_evidence_is_a_real_vocabulary_term(text: str) -> None:
    """Evidence must be auditable: a term in the output that is not in the vocabulary is a bug."""
    for group, terms in score(text).evidence.items():
        assert set(terms) <= VOCABULARY[group]
        assert terms


@given(text=st.text(max_size=200))
def test_relevance_follows_exactly_from_the_documented_thresholds(text: str) -> None:
    result = score(text)
    deepest = max((len(terms) for terms in result.evidence.values()), default=0)
    expected = len(result.matched_groups) >= MIN_GROUPS or deepest >= MIN_TERMS_IN_ONE_GROUP
    assert result.relevant is expected


@given(text=st.text(max_size=150), noise=st.text(alphabet="  \n\t.,;:!?()[]-", max_size=30))
def test_adding_only_punctuation_and_space_cannot_create_relevance(text: str, noise: str) -> None:
    if not score(text).relevant:
        assert not score(f"{text}{noise}").relevant
