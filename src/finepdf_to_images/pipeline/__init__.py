"""The five stages, each in its own module.

This was one 895-line file holding every stage and 24 helpers. The helpers were stage-local almost
without exception — reading one stage meant scrolling past four others, and every change landed in
the same file, so unrelated work collided there.

The public surface is unchanged: ``from finepdf_to_images.pipeline import run_publish`` still
resolves, so no caller or test had to move.
"""

from finepdf_to_images.pipeline.extract import ExtractionResult, run_extract
from finepdf_to_images.pipeline.publish import PublicationResult, run_publish
from finepdf_to_images.pipeline.retrieve import RetrievalResult, run_retrieve
from finepdf_to_images.pipeline.score import ScoringResult, run_score
from finepdf_to_images.pipeline.select import SelectionResult, run_select
from finepdf_to_images.pipeline.shared import (
    DOCUMENTS_NAME,
    IMAGES_NAME,
    MANIFEST_NAME,
    RECORDS_NAME,
    RETRIEVED_NAME,
    SCORED_NAME,
)

__all__ = [
    "DOCUMENTS_NAME",
    "IMAGES_NAME",
    "MANIFEST_NAME",
    "RECORDS_NAME",
    "RETRIEVED_NAME",
    "SCORED_NAME",
    "ExtractionResult",
    "PublicationResult",
    "RetrievalResult",
    "ScoringResult",
    "SelectionResult",
    "run_extract",
    "run_publish",
    "run_retrieve",
    "run_score",
    "run_select",
]
