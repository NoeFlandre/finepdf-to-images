"""Agriculture relevance scoring."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.scoring import (
    EXCLUDED_AMBIGUOUS_TERMS,
    MIN_CONCEPTS_IN_ONE_GROUP,
    MIN_GROUPS,
    VOCABULARY,
    VOCABULARY_LANGUAGE,
    normalize,
    score,
    vocabulary_summary,
)
from finepdf_to_images.domain.serialization import canonical_bytes

AGRICULTURE = (
    "Irrigation scheduling for maize and wheat under drought stress. Soil moisture and "
    "fertilizer application were measured across the farmland."
)
UNRELATED = (
    "Quarterly municipal council agenda. Call to order, land acknowledgement, attendance, "
    "approval of minutes, and the treasurer's report on parking revenue."
)

ALL_FORMS = {
    form for concepts in VOCABULARY.values() for forms in concepts.values() for form in forms
}


# --------------------------------------------------------------------------- the headline cases


def test_agriculture_text_is_relevant_with_evidence() -> None:
    result = score(AGRICULTURE)
    assert result.relevant
    assert result.score >= MIN_GROUPS
    assert result.evidence["irrigation"]["irrigation"] == ("irrigation",)
    assert result.evidence["crops"]["maize"] == ("maize",)
    assert result.evidence["soil"]["soil_moisture"] == ("soil moisture",)


def test_unrelated_text_is_not_relevant() -> None:
    result = score(UNRELATED)
    assert not result.relevant
    assert result.matched_groups == ()
    assert result.evidence == {}
    assert result.matched_terms == ()


@pytest.mark.parametrize(
    # Escaped rather than literal so the source file itself stays free of ambiguous characters:
    # em dash, en dash, ellipsis, NUL.
    "text",
    ["", "   ", "\n\t  \n", "...", "— – …", "\x00", "___", "12345"],
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
        # REGRESSION: these five were all classified as agriculture.
        "Enable structured logging across the server farm before the release.",
        "Well logging showed the water table at 12 m depth.",
        "Timber supports in the shaft; the water table was monitored.",
        "Potato salad, rice bowls and corn chips: a restaurant menu with grain bread.",
        "We farm out the work to a contractor and farm the logs into shards.",
    ],
)
def test_ambiguous_words_alone_do_not_make_a_document_agricultural(text: str) -> None:
    """Each of these is a document about software, geology, construction or food."""
    result = score(text)
    assert not result.relevant, result.evidence


@pytest.mark.parametrize("term", sorted(EXCLUDED_AMBIGUOUS_TERMS))
def test_no_excluded_term_is_a_surface_form(term: str) -> None:
    """REGRESSION: the exclusion list used to be documentation nothing checked."""
    assert term not in ALL_FORMS


def test_every_exclusion_records_why() -> None:
    assert all(reason for reason in EXCLUDED_AMBIGUOUS_TERMS.values())


def test_the_import_time_guard_actually_fires() -> None:
    """The guard is only worth having if it would catch a term being added back."""
    from finepdf_to_images.domain import scoring

    original = scoring.VOCABULARY
    try:
        scoring.VOCABULARY = {"crops": {"corn": frozenset({"corn"})}}
        with pytest.raises(AssertionError, match="back in the vocabulary"):
            scoring._assert_exclusions_hold()
    finally:
        scoring.VOCABULARY = original
    scoring._assert_exclusions_hold()


@pytest.mark.parametrize(
    ("text", "form"),
    [
        ("wheaten bread for breakfast", "wheat"),
        ("the goatee and the barleycorn", "goat"),
        ("irrigational studies", "irrigation"),
        ("a maizelike pattern", "maize"),
        ("silageless winters", "silage"),
    ],
)
def test_terms_match_whole_words_not_substrings(text: str, form: str) -> None:
    """Substring matching would make "wheat" match "wheaten" and "goat" match "goatee"."""
    assert form in ALL_FORMS, "the fixture must use a real vocabulary term to mean anything"
    assert form not in score(text).matched_terms


@pytest.mark.parametrize("form", ["maize", "wheat", "aquaculture"])
def test_a_term_at_the_very_start_or_end_still_matches(form: str) -> None:
    assert form in score(form).matched_terms


# --------------------------------------------------------------------------- subsumption


@pytest.mark.parametrize(
    ("text", "expected_groups"),
    [
        ("Pasture management notes.", ("livestock",)),
        ("Fish farming.", ("fisheries",)),
        ("Beef cattle only.", ("livestock",)),
        ("Selective logging report.", ("forestry",)),
    ],
)
def test_one_phrase_cannot_satisfy_the_breadth_rule_on_its_own(
    text: str, expected_groups: tuple[str, ...]
) -> None:
    """REGRESSION: `pasture` inside `pasture management` and `farming` inside `fish farming` each
    produced two groups from a single phrase, defeating MIN_GROUPS."""
    result = score(text)
    assert result.matched_groups == expected_groups
    assert not result.relevant


def test_a_subsumed_form_is_absent_from_the_evidence() -> None:
    result = score("Pasture management notes.")
    assert result.matched_terms == ("pasture management",)


@pytest.mark.parametrize(
    ("text", "expected_groups"),
    [
        ("Fish farming and dryland farming in the district.", ("farm_management", "fisheries")),
        ("Drip irrigation, and irrigation more broadly.", ("irrigation",)),
        ("Pasture management on the pasture beyond the ridge.", ("livestock",)),
    ],
)
def test_a_short_form_survives_where_it_occurs_independently(
    text: str, expected_groups: tuple[str, ...]
) -> None:
    """REGRESSION: subsumption was set-based, so `fish farming` appearing anywhere deleted the
    standalone `farming` in "dryland farming" and erased a whole group of real evidence."""
    assert score(text).matched_groups == expected_groups


def test_matching_consumes_text_so_one_phrase_is_not_counted_twice() -> None:
    result = score("Pasture management notes.")
    assert result.matched_terms == ("pasture management",)


@pytest.mark.parametrize(
    "text",
    [
        "Commodity index weights: maize, soybean, sugarcane, wheat.",
        "Futures on wheat, barley, sorghum and millet settled lower.",
        "Menu: paddy, millet, sorghum and cassava dishes.",
    ],
)
def test_a_bare_list_of_commodity_names_does_not_reach_the_depth_threshold(text: str) -> None:
    """REGRESSION: four crop names cleared the depth rule, so a price list scored as agriculture."""
    result = score(text)
    assert result.concept_depth >= MIN_CONCEPTS_IN_ONE_GROUP
    assert not result.relevant, result.evidence


def test_depth_counts_once_a_practice_concept_joins_the_commodity_names() -> None:
    result = score("Wheat, barley and sorghum cultivar trials.")
    assert result.matched_groups == ("crops",)
    assert result.relevant


def test_agrochemical_classes_are_separate_concepts() -> None:
    """A document covering herbicides and fungicides is two ideas, not one."""
    result = score("Herbicide and fungicide residues were measured.")
    assert set(result.evidence["farm_management"]) == {"fungicide", "herbicide"}


def test_subsumption_keeps_genuinely_separate_phrases() -> None:
    result = score("Pasture management and drip irrigation on the same holding.")
    assert result.matched_terms == ("drip irrigation", "pasture management")
    assert result.relevant


# --------------------------------------------------------------------------- the thresholds


def test_a_single_incidental_group_is_not_enough() -> None:
    result = score("The vineyard was mentioned once in passing.")
    assert result.matched_groups == ("crops",)
    assert not result.relevant


def test_two_groups_are_enough() -> None:
    result = score("The farmer uses drip irrigation.")
    assert len(result.matched_groups) >= MIN_GROUPS
    assert result.relevant


def test_enough_concept_depth_in_one_group_is_enough() -> None:
    """A narrowly focused document -- a wheat agronomy paper -- that breadth alone would miss."""
    result = score("Wheat, barley and sorghum cultivar trials with staggered sowing.")
    assert result.matched_groups == ("crops",)
    assert result.concept_depth >= MIN_CONCEPTS_IN_ONE_GROUP
    assert result.relevant


@pytest.mark.parametrize(
    "text",
    [
        "drip irrigation and irrigated plots under an irrigation scheme",
        "Farmers who farmed, farmland and farming.",
        "fertilizer, fertiliser, fertilizers and fertilisers",
        "the fishery and the fisheries",
    ],
)
def test_inflections_of_one_concept_do_not_reach_the_depth_threshold(text: str) -> None:
    """REGRESSION: surface forms were counted as distinct concepts, so one idea mentioned four
    ways cleared a threshold meant to require three different ideas."""
    result = score(text)
    assert result.concept_depth == 1
    assert not result.relevant


# --------------------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "text",
    [
        "IRRIGATION of MAIZE",
        "irrigation, maize.",
        "  irrigation\n\nmaize  ",
        "irrigation---maize",
        "(irrigation) [maize]",
        # REGRESSION: \w keeps the underscore, so these were silently unmatchable.
        "irrigation_maize",
        "soil_moisture is not it, but irrigation_maize",
        # Full-width Latin: "IRRIGATION MAIZE", which NFKC folds onto the plain letters.
        "ＩＲＲＩＧＡＴＩＯＮ ＭＡＩＺＥ",
    ],
)
def test_case_punctuation_and_typographic_variants_do_not_change_the_result(text: str) -> None:
    result = score(text)
    assert "crops" in result.matched_groups
    assert "irrigation" in result.matched_groups


def test_underscored_phrases_match() -> None:
    """REGRESSION: extracted PDF text, table headers and code listings use underscores."""
    assert "soil moisture" in score("soil_moisture sensor readings").matched_terms


def test_repeating_a_term_does_not_inflate_the_score() -> None:
    once = score("irrigation maize")
    many = score("irrigation maize " * 40)
    assert once.score == many.score
    assert once.evidence == many.evidence


def test_normalize_is_idempotent() -> None:
    assert normalize(normalize("Maize, Wheat!")) == normalize("Maize, Wheat!")


def test_a_phrase_matches_across_punctuation_but_not_across_a_gap() -> None:
    assert "soil moisture" in score("soil, moisture").matched_terms
    assert "soil moisture" not in score("soil in the moisture").matched_terms


def test_a_phrase_matches_across_a_line_break() -> None:
    assert "drip irrigation" in score("drip\nirrigation").matched_terms


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
    assert list(result.matched_terms) == sorted(result.matched_terms)
    for concepts in result.evidence.values():
        assert list(concepts) == sorted(concepts)
        for forms in concepts.values():
            assert list(forms) == sorted(forms)


def test_the_summary_documents_the_vocabulary_and_thresholds() -> None:
    summary = vocabulary_summary()
    assert summary["language"] == VOCABULARY_LANGUAGE
    assert summary["thresholds"] == {
        "min_groups": MIN_GROUPS,
        "min_concepts_in_one_group": MIN_CONCEPTS_IN_ONE_GROUP,
    }
    assert set(summary["groups"]) == set(VOCABULARY)
    assert summary["concept_count"] == sum(len(c) for c in VOCABULARY.values())
    assert summary["surface_form_count"] == len(ALL_FORMS)
    assert summary["excluded_ambiguous_terms"]["corn"]


# --------------------------------------------------------------------------- properties


@pytest.mark.property
@given(text=st.text(max_size=400))
def test_scoring_arbitrary_unicode_never_raises(text: str) -> None:
    result = score(text)
    assert isinstance(result.relevant, bool)
    assert result.score >= 0


@pytest.mark.property
@given(text=st.text(max_size=200))
def test_scoring_is_deterministic_for_any_input(text: str) -> None:
    assert score(text).as_dict() == score(text).as_dict()


@pytest.mark.property
@given(text=st.text(max_size=200), pad=st.sampled_from(["", " ", "\n", "\t", "   \n\t "]))
def test_surrounding_whitespace_never_changes_the_decision(text: str, pad: str) -> None:
    assert score(f"{pad}{text}{pad}").relevant == score(text).relevant


@pytest.mark.property
@given(text=st.text(max_size=200))
def test_the_score_never_exceeds_the_number_of_groups(text: str) -> None:
    result = score(text)
    assert result.score == len(result.matched_groups) <= len(VOCABULARY)
    assert set(result.matched_groups) == set(result.evidence)


@pytest.mark.property
@given(text=st.text(max_size=200))
def test_every_piece_of_evidence_is_a_real_vocabulary_term(text: str) -> None:
    """Evidence must be auditable: a term in the output that is not in the vocabulary is a bug."""
    result = score(text)
    assert set(result.matched_terms) <= ALL_FORMS
    for group, concepts in result.evidence.items():
        for concept, forms in concepts.items():
            assert forms, "an empty concept must not appear in the evidence"
            assert set(forms) <= VOCABULARY[group][concept]


@pytest.mark.property
@given(text=st.text(max_size=200))
def test_no_matched_term_is_subsumed_by_another(text: str) -> None:
    terms = score(text).matched_terms
    for term in terms:
        assert not any(other != term and f" {term} " in f" {other} " for other in terms)


@pytest.mark.property
@given(text=st.text(max_size=200))
def test_relevance_follows_exactly_from_the_documented_thresholds(text: str) -> None:
    result = score(text)
    expected = (
        len(result.matched_groups) >= MIN_GROUPS
        or result.concept_depth >= MIN_CONCEPTS_IN_ONE_GROUP
    )
    assert result.relevant is expected


@pytest.mark.property
@given(text=st.text(max_size=150), noise=st.text(alphabet="  \n\t.,;:!?()[]-_", max_size=30))
def test_adding_only_punctuation_and_space_cannot_create_relevance(text: str, noise: str) -> None:
    if not score(text).relevant:
        assert not score(f"{text}{noise}").relevant
