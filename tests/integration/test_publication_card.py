"""The generated dataset card."""

from __future__ import annotations

import pytest

from finepdf_to_images.adapters.hub import FakeHub
from finepdf_to_images.domain.publication import (
    DATASET_FILE,
    PublicationError,
)
from finepdf_to_images.pipeline import run_publish
from tests.integration.publication_support import (  # noqa: F401
    _card,
    _cleared_run,
    _fixture_image_root,
    _manifest_for,
    _publish_cleared,
    _published_rows,
    publish,
    run_cli,
    staged_inputs,
)
from tests.unit.test_publication_domain import (
    SAMPLING,
    SOURCE,
    document_row,
    image_row,
    retrieved_row,
    scored_row,
)

pytestmark = pytest.mark.integration

SELECT_MANIFEST = {"stage": "select", "source": SOURCE, "sampling": SAMPLING}

EXTRACT_MANIFEST = {"stage": "extract", "encoder": {"pypdf": "6.19.0", "pillow": "12.3.0"}}

CLEARED_PUBLICATION = {
    "disposition": "publish-artifact",
    "reason": "public-domain confirmed by curated-allowlist",
    "license": {
        "status": "declared-open",
        "identifier": "public-domain",
        "evidence": "curated-allowlist",
        "note": "ntp.niehs.nih.gov: 17 U.S.C. 105",
    },
}


def test_the_card_front_matter_is_valid_yaml() -> None:
    """Ported from the removed render_card tests.

    The front matter is a machine-read contract, not prose: the Hub parses it to decide how to
    load the dataset, and invalid YAML fails the viewer outright rather than degrading.
    """
    import yaml

    hub = FakeHub()
    publish(hub, apply=True)
    front = yaml.safe_load(_card(hub).split("---")[1])
    assert front["configs"][0]["config_name"] == "default"
    assert front["configs"][0]["data_files"][0]["path"] == DATASET_FILE


def test_the_card_declares_the_image_column_as_a_scalar_image() -> None:
    """Ported. Without a declared image dtype the viewer shows a struct instead of a picture.

    Scalar rather than a sequence since #38: a list of images renders as JSON.
    """
    import yaml

    hub = FakeHub()
    publish(hub, apply=True)
    features = yaml.safe_load(_card(hub).split("---")[1])["dataset_info"]["features"]
    typed = {entry["name"]: entry for entry in features}
    assert typed["image"]["dtype"] == "image"
    assert "sequence" not in typed["image"]
    assert set(typed) == {"pdf_url", "image", "caption", "text", "matched_terms"}


def test_the_front_matter_declares_exactly_the_published_columns() -> None:
    """REGRESSION: the features block and `DATASET_FIELDS` drifted when `caption` was added.

    The card's schema *table* is generated from `DATASET_FIELDS`; the front matter is written out
    by hand, and nothing compared the two. A column the front matter omits is a column the Hub
    does not type -- which for `image` is the difference between a thumbnail and a struct.
    """
    import yaml

    from finepdf_to_images.domain.publication import DATASET_FIELDS

    hub = FakeHub()
    publish(hub, apply=True)
    features = yaml.safe_load(_card(hub).split("---")[1])["dataset_info"]["features"]

    assert [entry["name"] for entry in features] == [name for name, _ in DATASET_FIELDS]


def test_the_card_is_deterministic() -> None:
    """Ported. A card that varies between runs breaks the no-op guarantee."""
    first, second = FakeHub(), FakeHub()
    publish(first, apply=True)
    publish(second, apply=True)
    assert _card(first) == _card(second)


def test_the_card_states_the_odc_by_attribution_obligation() -> None:
    """Ported. Attribution to the upstream dataset is an obligation, not decoration."""
    hub = FakeHub()
    publish(hub, apply=True)
    card = _card(hub)
    assert "ODC-BY" in card
    assert "HuggingFaceFW/finepdfs" in card


def test_a_plan_is_byte_identical_for_the_same_inputs() -> None:
    """Ported. This is what makes a second --apply an exact no-op."""
    first, second = FakeHub(), FakeHub()
    a = publish(first, apply=True)
    b = publish(second, apply=True)
    assert a.plan.digest == b.plan.digest
    assert [f.sha256 for f in a.plan.files] == [f.sha256 for f in b.plan.files]


@pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "/b", "a/"])
def test_an_invalid_destination_is_refused(repo: str) -> None:
    """Ported. The destination is interpolated into API calls, so it is validated not trusted."""
    with pytest.raises(PublicationError):
        run_publish(
            hub=FakeHub(),
            repo=repo,
            select_manifest=SELECT_MANIFEST,
            scored=[scored_row(0, relevant=True)],
            retrieved=[retrieved_row(0)],
            documents=[document_row(0)],
            images=[image_row(0)],
            extract_manifest=EXTRACT_MANIFEST,
            image_root=_fixture_image_root(),
        )


def test_the_card_explains_the_relevance_rule_and_the_filters() -> None:
    """A reader should be able to tell why a document is here without reading the code.

    Generated from vocabulary_summary() and MIN_IMAGE_SIDE rather than written, so the described
    rule cannot drift from the one that selected the rows -- the same reason the rest of the card
    is generated.
    """
    from finepdf_to_images.domain.images import MIN_IMAGE_SIDE
    from finepdf_to_images.domain.publication.rows import MIN_PAGES_FOR_FURNITURE
    from finepdf_to_images.domain.scoring import vocabulary_summary

    hub = FakeHub()
    publish(hub, apply=True)
    card = _card(hub)
    vocabulary = vocabulary_summary()

    assert "## How a row got here" in card
    assert f"**{vocabulary['surface_form_count']} phrases**" in card
    assert f"**{vocabulary['concept_count']} concepts**" in card
    assert f"at least\n{vocabulary['thresholds']['min_groups']} different groups" in card
    assert "keyword filter over English text" in card
    assert "only covers English" in card
    assert f"**{MIN_IMAGE_SIDE}px on either side**" in card
    assert f"**{MIN_PAGES_FOR_FURNITURE} or more pages**" in card
    assert "at least one phenotyping concept" in card
    assert "Every row is an image and the caption its author wrote for it" in card
    assert "scan of pages" in card


def test_the_card_section_tracks_the_scorer_rather_than_repeating_it() -> None:
    """If the vocabulary grows, the card says so without anyone editing it."""
    from finepdf_to_images.domain.scoring import vocabulary_summary

    hub = FakeHub()
    publish(hub, apply=True)
    stated = int(_card(hub).split(" phrases**")[0].rsplit("**", 1)[1])
    assert stated == vocabulary_summary()["surface_form_count"]
