"""What the published dataset is: its identity, its limits, and its column schema.

The values every other module in this package reads. Kept apart so that a constant cannot
acquire a dependency: this module imports nothing from the package, which is what lets the row
builders, the plan and the card all read it without a cycle.
"""

from __future__ import annotations

import re

#: Bumped when the published row shape changes. Consumers index on these names.
SCHEMA_VERSION = 3

DEFAULT_REPO = "NoeFlandre/finepdf-to-images-poc"

#: Hard ceiling on total published text bytes across all documents.
#: The 1000-row pilot yields ~24 MB; 50 MB prevents unbounded text payload dumps.
MAX_DOCUMENT_TEXT_BYTES = 50 * 1024 * 1024

#: Hard ceiling on total published artifact bytes -- images and PDFs together.
#:
#: Unlike the text cap this bounds *third-party works*, so it is deliberately small. The pilot's
#: three allow-listed sources come to under 2 MB; 64 MB leaves room to grow without any chance of
#: a mistake in the allow list quietly turning into a multi-gigabyte redistribution.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

#: ``namespace/name`` as the Hub spells it. The destination is interpolated into API calls and
#: printed in the card, so it is validated rather than trusted -- the same rule SourceRef applies
#: to where the data comes *from*.
#: A SHA-256 hex digest. Used to refuse artifact paths that identify nothing.
_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")

_REPO_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,94}/[A-Za-z0-9][A-Za-z0-9._-]{0,94}\Z")

#: ``MANIFEST_FILE`` is no longer published. It survives because ``OWNED_FILES`` needs it so a
#: publication can still delete the manifest.json the six-file layout left on the Hub.
MANIFEST_FILE = "manifest.json"
CARD_FILE = "README.md"

#: The published dataset: one parquet, one row per relevant document.
#:
#: The six-file JSONL layout this replaces was shaped by the pipeline's stages rather than by a
#: reader: 203 files, 18 columns of bookkeeping, and 948 of 1000 rows that a reader has no use
#: for. Images were loose files addressed by digest, invisible in the viewer and reachable only by
#: joining two JSONL files by hand.
DATASET_FILE = "data/train-00000-of-00001.parquet"

#: The published columns, as (field, description). Emitted into the card, so the documented
#: schema is generated from the same tuple the rows are built from and cannot drift from it.

DATASET_FIELDS: tuple[tuple[str, str], ...] = (
    ("pdf_url", "the source PDF this image came from"),
    ("image", "the image itself"),
    ("caption", "the figure caption written on the image's page, when the page names one"),
    ("text", "text extracted from that PDF"),
    ("matched_terms", "the vocabulary terms that made the document relevant"),
)


class PublicationError(ValueError):
    """Inputs that cannot be published as they stand."""
