"""Shared setup for the publication-stage tests.

Split out of a single 1170-line module. These helpers are used by several of those files, so
they live in one place rather than being copied between test modules.
"""

from __future__ import annotations

import functools
import json
import pathlib
import tempfile
from typing import Any

import pytest

from finepdf_to_images.adapters.hub import FakeHub
from finepdf_to_images.domain.images import image_path
from finepdf_to_images.domain.publication import (
    CARD_FILE,
    DATASET_FILE,
)
from finepdf_to_images.domain.retrieval import artifact_path
from finepdf_to_images.domain.serialization import sha256_hex
from finepdf_to_images.pipeline import PublicationResult, run_publish
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


#: Set by the session fixture in conftest.py to a directory pytest owns and cleans up.
TMP_ROOT: pathlib.Path | None = None


def scratch(name: str) -> pathlib.Path:
    """A fresh directory under the session's pytest-managed temp root.

    Falls back to a plain temp directory only when used outside a pytest session, so importing
    this module never depends on the fixture having run.
    """
    if TMP_ROOT is None:  # pragma: no cover - only outside a pytest session
        return pathlib.Path(tempfile.mkdtemp(prefix=f"finepdf-{name}-"))
    root = TMP_ROOT / name
    root.mkdir(parents=True, exist_ok=True)
    return root


@functools.cache
def _fixture_image_root() -> pathlib.Path:
    """An image tree holding the bytes ``image_row(0)`` names, built once.

    Every extracted image is now published, so the stage requires an image root whenever the run
    extracted anything -- there is no longer a licence filter that leaves the fixture's image
    unshipped. The bytes are the digest's own preimage so the stage's digest check passes.
    """
    root = scratch("images")
    data = b"img-0-0-0"
    target = root / image_path(sha256_hex(data), "image/png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return root


def publish(
    hub: FakeHub,
    *,
    apply: bool = False,
    out_dir: pathlib.Path | None = None,
    documents: int = 2,
) -> PublicationResult:
    return run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        scored=[scored_row(i, relevant=i == 0) for i in range(documents)],
        retrieved=[retrieved_row(0)],
        documents=[document_row(0)],
        images=[image_row(0)],
        extract_manifest=EXTRACT_MANIFEST,
        apply=apply,
        out_dir=out_dir,
        image_root=_fixture_image_root(),
    )


def _published_rows(hub: FakeHub) -> list[dict[str, Any]]:
    """The published table, read back the way a consumer reads it."""
    import io

    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(hub.files[DATASET_FILE])).to_pylist()


def _card(hub: FakeHub) -> str:
    return hub.files[CARD_FILE].decode()


def run_cli(args: list[str]) -> int:
    from finepdf_to_images.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def staged_inputs(tmp_path: pathlib.Path) -> list[str]:
    def write(name: str, payload: object) -> str:
        path = tmp_path / name
        if isinstance(payload, list):
            path.write_text("".join(json.dumps(row) + "\n" for row in payload), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    return [
        "--select-manifest",
        write("select.json", SELECT_MANIFEST),
        "--scored",
        write("scored.jsonl", [scored_row(0)]),
        "--retrieved",
        write("retrieved.jsonl", [retrieved_row(0)]),
        "--documents",
        write("documents.jsonl", [document_row(0)]),
        "--images",
        write("images.jsonl", [image_row(0)]),
        "--extract-manifest",
        write("extract.json", EXTRACT_MANIFEST),
        # Required now that every extracted image is published rather than only cleared ones.
        "--image-root",
        str(_fixture_image_root()),
        "--repo",
        "a/b",
    ]


def _cleared_run(tmp_path: pathlib.Path, *, pdf: bytes, image: bytes) -> dict[str, Any]:
    """A one-document run whose artifact bytes are on disk where the stage expects them."""
    retrieval = {**retrieved_row(0), "publication": CLEARED_PUBLICATION}
    retrieval["sha256"] = sha256_hex(pdf)
    document = {**document_row(0), "pdf_sha256": sha256_hex(pdf)}
    picture = {
        **image_row(0),
        "sha256": sha256_hex(image),
        "pdf_sha256": sha256_hex(pdf),
    }

    pdf_root = tmp_path / "retrieve"
    image_root = tmp_path / "extract"
    pdf_file = pdf_root / artifact_path(sha256_hex(pdf))
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    pdf_file.write_bytes(pdf)
    image_file = image_root / image_path(sha256_hex(image), "image/png")
    image_file.parent.mkdir(parents=True, exist_ok=True)
    image_file.write_bytes(image)

    return {
        "scored": [scored_row(0, relevant=True)],
        "retrieved": [retrieval],
        "documents": [document],
        "images": [picture],
        "pdf_root": pdf_root,
        "image_root": image_root,
    }


def _publish_cleared(hub: FakeHub, run: dict[str, Any], **overrides: Any) -> PublicationResult:
    return run_publish(
        hub=hub,
        repo="NoeFlandre/finepdf-to-images-poc",
        select_manifest=SELECT_MANIFEST,
        scored=run["scored"],
        retrieved=run["retrieved"],
        documents=run["documents"],
        images=run["images"],
        extract_manifest=EXTRACT_MANIFEST,
        pdf_root=run["pdf_root"],
        image_root=run["image_root"],
        **overrides,
    )


def _manifest_for(documents: list[Any], images: list[Any]) -> dict[str, Any]:
    """An extract manifest that honestly describes these two row sets."""
    from finepdf_to_images.domain.serialization import content_digest

    return {
        **EXTRACT_MANIFEST,
        "documents_digest": content_digest(documents),
        "images_digest": content_digest(images),
    }
