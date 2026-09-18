"""The published dataset: its schema, its card, and what a publication would upload.

Pure. Assembling the rows, rendering the card and deciding which files a publication consists of
all happen here over plain values; talking to the Hub is the adapter's job. That split is what
makes "dry-run does not mutate the Hub" a structural fact rather than a promise -- the dry run
simply never reaches the adapter's write path.

The package keeps the jobs apart: :mod:`schema` holds the constants and the error, :mod:`fields`
reads a stage's output strictly, :mod:`rows` turns that output into published rows, :mod:`card`
renders the markdown, :mod:`checks` refuses what may not be published, and :mod:`plan` says what
a publication would upload and remove.

Callers import from this package, not from the submodules.
"""

from __future__ import annotations

from finepdf_to_images.domain.publication.card import render_dataset_card
from finepdf_to_images.domain.publication.checks import (
    check_artifact_byte_cap,
    check_inputs_match_extraction,
    check_text_byte_cap,
)
from finepdf_to_images.domain.publication.plan import (
    HUB_MANAGED_FILES,
    OWNED_FILES,
    OWNED_PREFIXES,
    PublicationPlan,
    PublishFile,
    build_dataset_plan,
    build_manifest,
    is_noop,
    stale_paths,
)
from finepdf_to_images.domain.publication.rows import (
    all_image_digests,
    build_dataset_rows,
    build_document_rows,
    build_image_rows,
    cleared_pdf_digests,
    derive_relevant_rows,
    derive_retrieved_rows,
)
from finepdf_to_images.domain.publication.schema import (
    CARD_FILE,
    DATASET_FIELDS,
    DATASET_FILE,
    DEFAULT_REPO,
    MANIFEST_FILE,
    MAX_ARTIFACT_BYTES,
    MAX_DOCUMENT_TEXT_BYTES,
    SCHEMA_VERSION,
    PublicationError,
)

__all__ = [
    "CARD_FILE",
    "DATASET_FIELDS",
    "DATASET_FILE",
    "DEFAULT_REPO",
    "HUB_MANAGED_FILES",
    "MANIFEST_FILE",
    "MAX_ARTIFACT_BYTES",
    "MAX_DOCUMENT_TEXT_BYTES",
    "OWNED_FILES",
    "OWNED_PREFIXES",
    "SCHEMA_VERSION",
    "PublicationError",
    "PublicationPlan",
    "PublishFile",
    "all_image_digests",
    "build_dataset_plan",
    "build_dataset_rows",
    "build_document_rows",
    "build_image_rows",
    "build_manifest",
    "check_artifact_byte_cap",
    "check_inputs_match_extraction",
    "check_text_byte_cap",
    "cleared_pdf_digests",
    "derive_relevant_rows",
    "derive_retrieved_rows",
    "is_noop",
    "render_dataset_card",
    "stale_paths",
]
