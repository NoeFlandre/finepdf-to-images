"""Agriculture relevance, scored transparently from extracted text.

Pure, deterministic, and deliberately unclever. The first version of a filter like this should be
something a domain expert can read, disagree with, and correct — not a model checkpoint whose
decisions nobody can audit. Every positive result carries the exact terms that produced it.

This is a **keyword filter over English text**, and the interface says so rather than pretending
otherwise: the result records the language it was told the document is in, and
:data:`VOCABULARY_LANGUAGE` states which language the vocabulary actually covers. Scoring a
Portuguese document against an English vocabulary is a miss, not a negative, and conflating the two
is how a pipeline quietly acquires a language bias it never declared.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

#: The language this vocabulary covers. Not the language of the document being scored.
VOCABULARY_LANGUAGE = "eng_Latn"

VOCABULARY_VERSION = 1

#: Concept groups. Breadth across groups is the signal that matters: a document that mentions soil,
#: irrigation and crops together is about agriculture; one that says "crop" once is probably about
#: photographs.
#:
#: Terms are matched as whole words or whole phrases after normalization, never as substrings —
#: otherwise "wheat" matches "wheaten", and worse, "cow" matches "coward".
VOCABULARY: Mapping[str, frozenset[str]] = {
    "crops": frozenset(
        {
            "agronomy",
            "arable",
            "barley",
            "cassava",
            "cereal",
            "corn",
            "cover crop",
            "crop rotation",
            "crop yield",
            "cropland",
            "cultivar",
            "grain",
            "harvest season",
            "legume",
            "maize",
            "millet",
            "oilseed",
            "orchard",
            "paddy",
            "pasture",
            "planting date",
            "potato",
            "rice",
            "sorghum",
            "soybean",
            "sowing",
            "sugarcane",
            "tillage",
            "vineyard",
            "wheat",
        }
    ),
    "livestock": frozenset(
        {
            "animal husbandry",
            "beef cattle",
            "broiler",
            "cattle",
            "dairy herd",
            "goat",
            "grazing",
            "livestock",
            "manure",
            "pasture management",
            "poultry",
            "ruminant",
            "silage",
            "stocking rate",
            "swine",
            "veterinary",
        }
    ),
    "soil": frozenset(
        {
            "agroforestry",
            "compost",
            "erosion control",
            "fertilizer",
            "fertiliser",
            "humus",
            "nitrogen fixation",
            "nutrient management",
            "soil erosion",
            "soil fertility",
            "soil moisture",
            "soil organic carbon",
            "soil ph",
            "soil sampling",
            "topsoil",
        }
    ),
    "irrigation": frozenset(
        {
            "crop water requirement",
            "drip irrigation",
            "evapotranspiration",
            "furrow irrigation",
            "irrigated",
            "irrigation",
            "rainfed",
            "sprinkler irrigation",
            "water table",
            "watershed management",
        }
    ),
    "forestry": frozenset(
        {
            "afforestation",
            "deforestation",
            "forest management",
            "logging",
            "reforestation",
            "silviculture",
            "timber",
            "tree plantation",
            "woodland",
        }
    ),
    "fisheries": frozenset(
        {
            "aquaculture",
            "fish farming",
            "fish pond",
            "fisheries",
            "fishery",
            "hatchery",
            "mariculture",
            "shellfish",
            "trawler",
        }
    ),
    "farm_management": frozenset(
        {
            "agricultural extension",
            "agricultural policy",
            "agriculture",
            "agricultural",
            "farm",
            "farmer",
            "farmers",
            "farming",
            "farmland",
            "food security",
            "pest management",
            "pesticide",
            "herbicide",
            "smallholder",
            "subsistence farming",
        }
    ),
}

#: Words deliberately **not** in the vocabulary, recorded so the omission reads as a decision
#: rather than an oversight. Each is common in agriculture and more common elsewhere:
#: crop (photographs), yield (finance, materials), field (physics, databases), plant (factories),
#: harvest (data), bull and bear (markets), seed (funding, randomness), culture (everything).
EXCLUDED_AMBIGUOUS_TERMS: frozenset[str] = frozenset(
    {"bear", "bull", "crop", "culture", "field", "harvest", "plant", "seed", "yield"}
)

#: A document matching this many distinct concept groups is relevant. Two rather than one, because
#: a single group is exactly what an incidental mention looks like.
MIN_GROUPS = 2

#: ...or this many distinct terms within a single group, which catches a narrowly focused document
#: (a wheat agronomy paper) that a breadth rule alone would miss.
MIN_TERMS_IN_ONE_GROUP = 3

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    """Fold ``text`` to a canonical, matchable form.

    NFKC first, so typographic variants ("ﬁ", full-width letters) collapse onto their plain
    equivalents; then casefold rather than lower, which handles ß and Turkish dotted I correctly;
    then every non-word run becomes a single space, which is what makes punctuation and line breaks
    invisible to matching without letting "crop," match as a different token than "crop".
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return f" {_NON_WORD.sub(' ', folded).strip()} "


def _terms_in(normalized: str, terms: Iterable[str]) -> list[str]:
    """Terms present as whole words or whole phrases, in sorted order for determinism."""
    return sorted(term for term in terms if f" {term} " in normalized)


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
    evidence: Mapping[str, tuple[str, ...]]
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
            "evidence": {group: list(terms) for group, terms in self.evidence.items()},
            "language": self.language,
            "vocabulary_language": self.vocabulary_language,
            "vocabulary_version": self.vocabulary_version,
            "language_matches_vocabulary": self.language_matches_vocabulary,
        }


def score(
    text: str,
    language: str = VOCABULARY_LANGUAGE,
    vocabulary: Mapping[str, frozenset[str]] = VOCABULARY,
) -> RelevanceResult:
    """Score one document's extracted text for agriculture relevance.

    Returns a valid negative for empty, whitespace-only, or unmatched text rather than raising:
    "this document is not about agriculture" is an ordinary answer, not an error.

    ``score`` is the number of distinct concept groups matched. A document is relevant when it
    matches at least :data:`MIN_GROUPS` groups, **or** at least :data:`MIN_TERMS_IN_ONE_GROUP`
    distinct terms inside a single group — the first rule catches breadth, the second catches a
    narrowly focused document that breadth alone would miss.
    """
    normalized = normalize(text)
    evidence = {}
    for group in sorted(vocabulary):
        matched = _terms_in(normalized, vocabulary[group])
        if matched:
            evidence[group] = tuple(matched)

    groups = tuple(sorted(evidence))
    deepest = max((len(terms) for terms in evidence.values()), default=0)
    relevant = len(groups) >= MIN_GROUPS or deepest >= MIN_TERMS_IN_ONE_GROUP

    return RelevanceResult(
        relevant=relevant,
        score=len(groups),
        matched_groups=groups,
        evidence=evidence,
        language=language,
    )


def vocabulary_summary(
    vocabulary: Mapping[str, frozenset[str]] = VOCABULARY,
) -> dict[str, Any]:
    """The vocabulary in machine-readable form, for the run manifest and the dataset card."""
    return {
        "version": VOCABULARY_VERSION,
        "language": VOCABULARY_LANGUAGE,
        "groups": {group: sorted(vocabulary[group]) for group in sorted(vocabulary)},
        "term_count": sum(len(terms) for terms in vocabulary.values()),
        "thresholds": {
            "min_groups": MIN_GROUPS,
            "min_terms_in_one_group": MIN_TERMS_IN_ONE_GROUP,
        },
        "excluded_ambiguous_terms": sorted(EXCLUDED_AMBIGUOUS_TERMS),
    }
