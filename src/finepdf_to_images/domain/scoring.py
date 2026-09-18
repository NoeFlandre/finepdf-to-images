"""Agriculture relevance, scored transparently from extracted text.

Pure, deterministic, and deliberately unclever. The first version of a filter like this should be
something a domain expert can read, disagree with, and correct — not a model checkpoint whose
decisions nobody can audit. Every positive result carries the exact terms that produced it.

This is a **keyword filter over English text**, and the interface says so rather than pretending
otherwise: the result records the language it was told the document is in, and
:data:`VOCABULARY_LANGUAGE` states which language the vocabulary actually covers. Scoring a
Portuguese document against an English vocabulary is a miss, not a negative, and conflating the two
is how a pipeline quietly acquires a language bias it never declared.

Structure
---------

The vocabulary is ``group -> concept -> surface forms``, not ``group -> terms``. The extra level is
load-bearing: an earlier flat version counted ``farm``, ``farmer``, ``farmers`` and ``farming`` as
four independent pieces of evidence, so "Farmers who farm and love farming" cleared a threshold
meant to require three *distinct concepts*. Depth counts concepts; surface forms are just spellings
of one.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

#: The language this vocabulary covers. Not the language of the document being scored.
VOCABULARY_LANGUAGE = "eng_Latn"

#: Bumped whenever the vocabulary or the thresholds change, so an earlier run's output stays
#: interpretable instead of being silently reinterpreted under new rules.
VOCABULARY_VERSION = 4

#: ``group -> concept -> surface forms``. Terms match as whole words or whole phrases after
#: normalization, never as substrings — otherwise "wheat" matches "wheaten" and "goat" matches
#: "goatee".
VOCABULARY: Mapping[str, Mapping[str, frozenset[str]]] = {
    "crops": {
        "agronomy": frozenset({"agronomy", "agronomic", "agronomist"}),
        "arable": frozenset({"arable", "arable land"}),
        "barley": frozenset({"barley"}),
        "cassava": frozenset({"cassava"}),
        "cover_crop": frozenset({"cover crop", "cover crops"}),
        "crop_rotation": frozenset({"crop rotation"}),
        "crop_yield": frozenset({"crop yield", "crop yields"}),
        "cropland": frozenset({"cropland", "croplands"}),
        "cultivar": frozenset({"cultivar", "cultivars"}),
        "harvest_season": frozenset({"harvest season", "harvesting season"}),
        "legume": frozenset({"legume", "legumes"}),
        "maize": frozenset({"maize"}),
        "millet": frozenset({"millet"}),
        "oilseed": frozenset({"oilseed", "oilseeds"}),
        "orchard": frozenset({"orchard", "orchards"}),
        "paddy": frozenset({"paddy", "paddy field", "paddy fields"}),
        "planting": frozenset({"planting date", "planting season", "sowing", "sowing date"}),
        "sorghum": frozenset({"sorghum"}),
        "soybean": frozenset({"soybean", "soybeans", "soya bean"}),
        "sugarcane": frozenset({"sugarcane", "sugar cane"}),
        "tillage": frozenset({"tillage", "conservation tillage", "no till"}),
        "vineyard": frozenset({"vineyard", "vineyards"}),
        "wheat": frozenset({"wheat"}),
    },
    "livestock": {
        "animal_husbandry": frozenset({"animal husbandry"}),
        "cattle": frozenset({"cattle", "beef cattle", "dairy cattle"}),
        "dairy_herd": frozenset({"dairy herd", "dairy herds"}),
        "goat": frozenset({"goat", "goats"}),
        "grazing": frozenset({"grazing", "grazing land", "overgrazing"}),
        "livestock": frozenset({"livestock"}),
        "manure": frozenset({"manure"}),
        "pasture": frozenset({"pasture", "pastures", "pastureland", "pasture management"}),
        "poultry": frozenset({"poultry", "broiler", "broilers"}),
        "ruminant": frozenset({"ruminant", "ruminants"}),
        "silage": frozenset({"silage"}),
        "stocking_rate": frozenset({"stocking rate"}),
        "swine": frozenset({"swine", "piggery"}),
        "veterinary": frozenset({"veterinary", "veterinarian"}),
    },
    "soil": {
        "agroforestry": frozenset({"agroforestry"}),
        "compost": frozenset({"compost", "composting"}),
        "erosion": frozenset({"soil erosion", "erosion control"}),
        "fertilizer": frozenset({"fertilizer", "fertiliser", "fertilizers", "fertilisers"}),
        "humus": frozenset({"humus"}),
        "nitrogen_fixation": frozenset({"nitrogen fixation"}),
        "nutrient_management": frozenset({"nutrient management"}),
        "soil_fertility": frozenset({"soil fertility"}),
        "soil_moisture": frozenset({"soil moisture"}),
        "soil_organic_carbon": frozenset({"soil organic carbon"}),
        "soil_ph": frozenset({"soil ph"}),
        "soil_sampling": frozenset({"soil sampling"}),
        "topsoil": frozenset({"topsoil"}),
    },
    "irrigation": {
        "crop_water_requirement": frozenset({"crop water requirement"}),
        "evapotranspiration": frozenset({"evapotranspiration"}),
        "irrigation": frozenset(
            {
                "irrigation",
                "irrigated",
                "drip irrigation",
                "furrow irrigation",
                "irrigation scheme",
                "sprinkler irrigation",
            }
        ),
        "rainfed": frozenset({"rainfed", "rain fed"}),
        "watershed_management": frozenset({"watershed management"}),
    },
    "forestry": {
        "afforestation": frozenset({"afforestation"}),
        "deforestation": frozenset({"deforestation"}),
        "forest_management": frozenset({"forest management", "forestry management"}),
        # Bare "logging" and "timber" are excluded; see EXCLUDED_AMBIGUOUS_TERMS.
        "logging": frozenset({"illegal logging", "logging concession", "selective logging"}),
        "reforestation": frozenset({"reforestation"}),
        "silviculture": frozenset({"silviculture"}),
        "timber": frozenset({"timber harvest", "timber plantation", "timber yield"}),
        "tree_plantation": frozenset({"tree plantation", "tree plantations"}),
        "woodland": frozenset({"woodland", "woodlands"}),
    },
    "fisheries": {
        "aquaculture": frozenset({"aquaculture"}),
        "fish_farming": frozenset({"fish farming", "fish farm", "fish farms"}),
        "fish_pond": frozenset({"fish pond", "fish ponds"}),
        "fisheries": frozenset({"fisheries", "fishery"}),
        "hatchery": frozenset({"hatchery", "hatcheries"}),
        "mariculture": frozenset({"mariculture"}),
        "shellfish": frozenset({"shellfish"}),
        "trawler": frozenset({"trawler", "trawlers"}),
    },
    "phenotyping": {
        "biomass": frozenset({"biomass", "above-ground biomass", "aboveground biomass"}),
        "canopy": frozenset({"canopy", "canopies", "canopy cover", "canopy closure"}),
        "chlorophyll": frozenset({"chlorophyll", "chlorophyll content"}),
        "cultivar_trial": frozenset(
            {"cultivar trial", "cultivar trials", "variety trial", "variety trials"}
        ),
        "field_trial": frozenset(
            {"field trial", "field trials", "replicated trial", "replicate plots"}
        ),
        "germplasm": frozenset({"germplasm", "accession", "accessions"}),
        "grain_yield": frozenset({"grain yield", "grain yields", "yield per plot"}),
        "growth_stage": frozenset(
            {"growth stage", "growth stages", "days after sowing", "days after planting"}
        ),
        "leaf_area": frozenset({"leaf area", "leaf area index", "lai"}),
        "ndvi": frozenset({"ndvi", "normalized difference vegetation index"}),
        "panicle": frozenset({"panicle", "panicles"}),
        "phenology": frozenset({"phenology", "phenological"}),
        "phenotype": frozenset({"phenotype", "phenotypes", "phenotyping", "phenotypic"}),
        "plant_height": frozenset({"plant height", "plant heights"}),
        "root_architecture": frozenset(
            {"root architecture", "root system architecture", "rooting depth"}
        ),
        "senescence": frozenset({"senescence", "senescent"}),
        "spectral": frozenset({"spectral reflectance", "hyperspectral", "multispectral"}),
        "stress_response": frozenset(
            {"drought stress", "heat stress", "water stress", "salinity stress"}
        ),
        "tiller": frozenset({"tiller", "tillers", "tillering"}),
    },
    "farm_management": {
        "agricultural_extension": frozenset({"agricultural extension"}),
        "agricultural_policy": frozenset({"agricultural policy"}),
        "agriculture": frozenset({"agriculture", "agricultural"}),
        # Bare "farm" is excluded (server farm, farm out); the inflections are unambiguous.
        "farming": frozenset({"farming", "farmer", "farmers", "farmland", "smallholder farming"}),
        "food_security": frozenset({"food security"}),
        "pest_management": frozenset({"pest management", "integrated pest management"}),
        "fungicide": frozenset({"fungicide", "fungicides"}),
        "herbicide": frozenset({"herbicide", "herbicides"}),
        "insecticide": frozenset({"insecticide", "insecticides"}),
        "pesticide": frozenset({"pesticide", "pesticides"}),
        "smallholder": frozenset({"smallholder", "smallholders"}),
    },
}

#: Terms deliberately **kept out** of the vocabulary, with the reason. Each is common in
#: agriculture and more common elsewhere. This is enforced, not decorative: the module refuses to
#: import if any of these appears as a surface form (see :func:`_assert_exclusions_hold`).
EXCLUDED_AMBIGUOUS_TERMS: Mapping[str, str] = {
    "bear": "markets",
    "bull": "markets",
    "cereal": "breakfast, food packaging",
    "corn": "food, commodity trading, idiom",
    "crop": "photographs, image editing",
    "culture": "organisational, microbiological, anthropological",
    "farm": "server farm, farm out, farm team",
    "field": "physics, databases, sport",
    "grain": "wood, photography, commodity shipping",
    "harvest": "data harvesting",
    "logging": "software logs, well logging",
    "plant": "factories, power plants",
    "potato": "food, menus",
    "rice": "food, menus, surnames",
    "seed": "funding rounds, randomness",
    "timber": "construction, mining supports",
    "water table": "hydrogeology, civil engineering",
    "yield": "finance, materials science",
}

#: Concepts that are bare commodity or species names. They are perfectly good *breadth* evidence,
#: but a list of them is not by itself a document about agriculture: "Commodity index weights:
#: maize, soybean, sugarcane, wheat" is a finance document, and a menu naming four grains is a
#: menu. The depth rule therefore requires at least one concept that is a practice, an input or a
#: management idea.
WEAK_CONCEPTS: Mapping[str, frozenset[str]] = {
    "crops": frozenset(
        {
            "barley",
            "cassava",
            "legume",
            "maize",
            "millet",
            "oilseed",
            "paddy",
            "sorghum",
            "soybean",
            "sugarcane",
            "wheat",
        }
    ),
    "livestock": frozenset({"cattle", "goat", "poultry", "ruminant", "swine"}),
    "fisheries": frozenset({"shellfish", "trawler"}),
}

#: A document matching this many distinct concept **groups** is relevant. Two rather than one,
#: because a single group is exactly what an incidental mention looks like.
MIN_GROUPS = 2

#: ...or this many distinct **concepts** within a single group, at least one of which is not a bare
#: commodity name (see :data:`WEAK_CONCEPTS`). This catches a narrowly focused document -- a wheat
#: agronomy paper -- that a breadth rule alone would miss, without admitting a price list. Concepts,
#: not surface forms: "farmer", "farmers" and "farming" are one concept mentioned three times.
MIN_CONCEPTS_IN_ONE_GROUP = 3

#: The group a document must touch to be relevant at all, whatever else it matches.
#:
#: Agriculture breadth alone admits documents that are genuinely agricultural and useless here: a
#: pesticide label cleared on `agricultural`, `fungicide`, `grazing`, and an outdoors magazine on
#: `goat`, `goats`, `shellfish`. Both are about agriculture; neither observes a plant. No threshold
#: over an agriculture vocabulary separates them, because the vocabulary has no word for the thing
#: that makes a document useful to this corpus -- a measured trait.
#:
#: So relevance is the conjunction: the agriculture rule establishes the context, and one
#: phenotyping concept establishes the topic. The second condition is what the dataset is for.
TOPIC_GROUP = "phenotyping"

#: ``\w`` includes the underscore and digits, so ``[^\w]+`` left "soil_moisture" glued together and
#: silently unmatchable. Extracted PDF text, table headers and code listings use underscores
#: routinely, so the underscore is an explicit separator here.
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _assert_exclusions_hold() -> None:
    """Refuse to import if an excluded term was added back as a surface form.

    Without this, ``EXCLUDED_AMBIGUOUS_TERMS`` would be a comment: the list documented an intent
    that nothing checked, and a later edit adding "corn" back would pass every test.
    """
    surface_forms = {
        form for concepts in VOCABULARY.values() for forms in concepts.values() for form in forms
    }
    readded = sorted(surface_forms & set(EXCLUDED_AMBIGUOUS_TERMS))
    if readded:
        raise AssertionError(
            f"terms excluded as ambiguous are back in the vocabulary: {readded}. "
            "Either use a disambiguating phrase or remove the entry from "
            "EXCLUDED_AMBIGUOUS_TERMS with a reason."
        )


_assert_exclusions_hold()


def normalize(text: str) -> str:
    """Fold ``text`` to a canonical, matchable form, padded with spaces at both ends.

    NFKC first, so typographic variants (ligatures, full-width letters) collapse onto their plain
    equivalents; then casefold rather than lower, which handles ß and Turkish dotted I correctly;
    then every non-word run — punctuation, whitespace, underscores — becomes a single space.

    The leading and trailing spaces are what make ``" term " in normalized`` a whole-word test, so
    a term at the very start or end of the text still matches and "wheat" never matches "wheaten".
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return f" {_NON_WORD.sub(' ', folded).strip()} "


#: Every surface form, for counting and for the exclusion guard.
ALL_SURFACE_FORMS: frozenset[str] = frozenset(
    form for concepts in VOCABULARY.values() for forms in concepts.values() for form in forms
)

#: Every surface form, longest first, so the longest phrase at a given position wins. Ties break
#: alphabetically to keep the scan deterministic.
_FORMS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(
        {form for concepts in VOCABULARY.values() for forms in concepts.values() for form in forms},
        key=lambda form: (-len(form), form),
    )
)

#: Stands in for text already claimed by a longer phrase. A character that cannot survive
#: :func:`normalize`, so it can never collide with real content.
_CLAIMED = "\x00"


def _matched_forms(normalized: str) -> set[str]:
    """Every vocabulary surface form the text contains, matched longest phrase first.

    Matching consumes text. Each form's occurrences are replaced with a placeholder before shorter
    forms are tried, so a short form is suppressed only where a longer one actually covered it —
    not merely because the longer one appeared somewhere else in the document.

    That distinction is the whole point. A set-based "drop anything contained in a longer match"
    rule erased real evidence: in "Fish farming and dryland farming in the district", ``farming``
    occurs standalone, but the presence of ``fish farming`` elsewhere deleted the farm-management
    group entirely and the document scored as not relevant.
    """
    working = normalized
    matched: set[str] = set()
    for form in _FORMS_LONGEST_FIRST:
        needle = f" {form} "
        if needle in working:
            matched.add(form)
            working = working.replace(needle, f" {_CLAIMED} ")
    return matched


def _evidence_for(surviving: set[str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Group the surviving surface forms by concept, dropping concepts nothing matched."""
    evidence: dict[str, dict[str, tuple[str, ...]]] = {}
    for group in sorted(VOCABULARY):
        concepts = {
            concept: tuple(sorted(forms & surviving))
            for concept, forms in sorted(VOCABULARY[group].items())
            if forms & surviving
        }
        if concepts:
            evidence[group] = concepts
    return evidence


def _has_real_depth(evidence: Mapping[str, Mapping[str, tuple[str, ...]]]) -> bool:
    """Whether some group is deep enough *and* not made only of bare commodity names.

    A list of four crop names is a price list or a menu, not a document about agriculture.
    """
    return any(
        len(concepts) >= MIN_CONCEPTS_IN_ONE_GROUP
        and not set(concepts).issubset(WEAK_CONCEPTS.get(group, frozenset()))
        for group, concepts in evidence.items()
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RelevanceResult:
    """A scoring decision and the complete evidence for it.

    ``language`` is what the caller said the document is, not a detection. It exists so that a
    result scored with an English vocabulary against a non-English document is visibly that, rather
    than an unexplained negative.
    """

    relevant: bool
    score: int
    matched_groups: tuple[str, ...]
    evidence: Mapping[str, Mapping[str, tuple[str, ...]]]
    matched_terms: tuple[str, ...]
    concept_depth: int
    language: str
    vocabulary_language: str = VOCABULARY_LANGUAGE
    vocabulary_version: int = VOCABULARY_VERSION

    @property
    def language_matches_vocabulary(self) -> bool:
        """Whether the vocabulary could meaningfully apply to this document at all."""
        return self.language == self.vocabulary_language

    def as_dict(self) -> dict[str, Any]:
        return {
            "relevant": self.relevant,
            "score": self.score,
            "matched_groups": list(self.matched_groups),
            "evidence": {
                group: {concept: list(forms) for concept, forms in concepts.items()}
                for group, concepts in self.evidence.items()
            },
            "matched_terms": list(self.matched_terms),
            "concept_depth": self.concept_depth,
            "language": self.language,
            "vocabulary_language": self.vocabulary_language,
            "vocabulary_version": self.vocabulary_version,
            "language_matches_vocabulary": self.language_matches_vocabulary,
        }


def score(text: str, language: str = VOCABULARY_LANGUAGE) -> RelevanceResult:
    """Score one document's extracted text for agriculture relevance.

    Returns a valid negative for empty, whitespace-only, or unmatched text rather than raising:
    "this document is not about agriculture" is an ordinary answer, not an error.

    ``score`` is the number of distinct concept groups matched. A document is relevant when it
    shows agricultural context — at least :data:`MIN_GROUPS` groups, **or** at least
    :data:`MIN_CONCEPTS_IN_ONE_GROUP` distinct concepts inside a single group, the first rule
    catching breadth and the second a narrowly focused document that breadth alone would miss —
    **and** it matches at least one concept in :data:`TOPIC_GROUP`.

    The conjunction is deliberate. The context rule alone admitted product labels and hunting
    magazines; the topic rule alone would admit any paper measuring biomass in a bioreactor.
    """
    surviving = _matched_forms(normalize(text))
    evidence = _evidence_for(surviving)

    groups = tuple(sorted(evidence))
    depth = max((len(concepts) for concepts in evidence.values()), default=0)
    context = len(groups) >= MIN_GROUPS or _has_real_depth(evidence)
    relevant = context and TOPIC_GROUP in evidence

    return RelevanceResult(
        relevant=relevant,
        score=len(groups),
        matched_groups=groups,
        evidence=evidence,
        matched_terms=tuple(sorted(surviving)),
        concept_depth=depth,
        language=language,
    )


def _serialized_vocabulary() -> dict[str, dict[str, list[str]]]:
    return {
        group: {concept: sorted(forms) for concept, forms in sorted(concepts.items())}
        for group, concepts in sorted(VOCABULARY.items())
    }


def vocabulary_summary() -> dict[str, Any]:
    """The vocabulary in machine-readable form, for the run manifest and the dataset card."""
    return {
        "version": VOCABULARY_VERSION,
        "language": VOCABULARY_LANGUAGE,
        "groups": _serialized_vocabulary(),
        "concept_count": sum(len(concepts) for concepts in VOCABULARY.values()),
        "surface_form_count": len(ALL_SURFACE_FORMS),
        "thresholds": {
            "min_groups": MIN_GROUPS,
            "min_concepts_in_one_group": MIN_CONCEPTS_IN_ONE_GROUP,
            "requires_phenotyping_concept": True,
        },
        "weak_concepts": {group: sorted(c) for group, c in sorted(WEAK_CONCEPTS.items())},
        "excluded_ambiguous_terms": dict(sorted(EXCLUDED_AMBIGUOUS_TERMS.items())),
    }
