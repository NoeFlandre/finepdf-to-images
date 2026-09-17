"""Executable acceptance scenarios for the whole pipeline.

The steps drive the **real** stage functions -- `run_select`, `run_score`, `run_retrieve`,
`run_extract` -- over the committed fixtures. Only the two external boundaries are substituted: the
shard comes from a local Parquet file and the documents from a fixture transport, so the scenarios
are deterministic and required CI reaches no third-party site.

The prose lives in ``features/pipeline.feature`` so the acceptance criteria can be read without
reading Python.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from finepdf_to_images.adapters.images import PypdfImageExtractor
from finepdf_to_images.adapters.retrieval import FixtureTransport, Response
from finepdf_to_images.adapters.source import LocalShardReader
from finepdf_to_images.adapters.storage import read_jsonl
from finepdf_to_images.domain import policy
from finepdf_to_images.domain.retrieval import PDF_MAGIC, artifact_path
from finepdf_to_images.domain.scoring import score
from finepdf_to_images.domain.serialization import sha256_hex
from finepdf_to_images.domain.source import SamplingSpec, SourceRef
from finepdf_to_images.pipeline import run_extract, run_retrieve, run_score, run_select

pytestmark = pytest.mark.acceptance

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
SHARDS = FIXTURES / "shards"
PDFS = FIXTURES / "pdfs"

HTML = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"

scenarios("features/pipeline.feature")


@dataclass
class World:
    """Everything the steps build up, so each step reads as one sentence."""

    tmp_path: pathlib.Path
    _transport: FixtureTransport | None = None
    selection: Any = None
    scoring: Any = None
    retrieval: Any = None
    extraction: Any = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    relevance: Any = None
    decision: Any = None

    @property
    def transport(self) -> FixtureTransport:
        """The fixture server. Asking for it before a Given set it up is a broken scenario."""
        assert self._transport is not None, "no fixture web server was set up for this scenario"
        return self._transport


@pytest.fixture
def world(tmp_path: pathlib.Path) -> World:
    return World(tmp_path=tmp_path)


def fixture_pdf(name: str) -> bytes:
    return (PDFS / name).read_bytes()


def url_for(row: dict[str, Any]) -> str:
    return str(row["url"])


# --------------------------------------------------------------------------- given


@given("a pinned FinePDFs shard fixture")
def _shard(world: World) -> None:
    assert (SHARDS / SourceRef().path).is_file()


@given("a fixture web server holding the source documents")
def _server(world: World) -> None:
    """Every fixture row gets a document. Row 3 gets one with no images, so the zero-image
    scenario is reachable from a real end-to-end run rather than from a special case."""
    responses: dict[str, Response] = {}
    for index in range(20):
        name = "no-images.pdf" if index == 3 else "two-images.pdf"
        responses[f"https://fixtures.invalid/doc-{index:04d}.pdf"] = Response(
            status=200, content_type="application/pdf", body=fixture_pdf(name)
        )
    world._transport = FixtureTransport(responses=responses)


@given(parsers.parse('a row whose url is "{url}"'))
def _row_with_url(world: World, url: str) -> None:
    world.rows = [{"row_index": 0, "row_id": "<urn:uuid:x>", "url": url}]


@given("a row whose document is served as HTML labelled application/pdf")
def _html_row(world: World) -> None:
    url = "https://fixtures.invalid/notpdf.pdf"
    world.rows = [{"row_index": 0, "row_id": "<urn:uuid:x>", "url": url}]
    world._transport = FixtureTransport(
        responses={url: Response(status=200, content_type="application/pdf", body=HTML)}
    )


@given("a retrieved document whose bytes are not a readable PDF")
def _malformed(world: World) -> None:
    world.retrieval = _stage_documents(world, ["malformed.pdf"])


@given("a retrieved document that contains no images")
def _no_images(world: World) -> None:
    world.retrieval = _stage_documents(world, ["no-images.pdf"])


@given("a row whose extracted text is blank")
def _blank(world: World) -> None:
    world.rows = [{"row_index": 0, "row_id": "<urn:uuid:x>", "text": "   \n\t ", "url": "u"}]


@given("a retrieved document with no established licence")
def _unlicensed(world: World) -> None:
    data = fixture_pdf("two-images.pdf")
    world.rows = [
        {
            "dataset": SourceRef().dataset,
            "revision": SourceRef().revision,
            "config": SourceRef().config,
            "split": SourceRef().split,
            "shard": SourceRef().shard,
            "row_index": 0,
            "row_id": "<urn:uuid:x>",
            "url": "https://fixtures.invalid/doc-0000.pdf",
            "sha256": sha256_hex(data),
        }
    ]


def _stage_documents(world: World, names: list[str]) -> Any:
    """Lay out a retrieve-stage output directly, for scenarios about extraction alone."""
    from finepdf_to_images.adapters.storage import write_bytes

    root = world.tmp_path / "retrieve"
    records = []
    for index, name in enumerate(names):
        data = fixture_pdf(name)
        digest = sha256_hex(data)
        write_bytes(root / artifact_path(digest), data)
        records.append(
            {
                "row_index": index,
                "row_id": f"<urn:uuid:{index:012d}>",
                "url": f"https://fixtures.invalid/{name}",
                "ok": True,
                "sha256": digest,
                "path": artifact_path(digest),
            }
        )
    return _Staged(root=root, records=records)


@dataclass
class _Staged:
    root: pathlib.Path
    records: list[dict[str, Any]]

    @property
    def records_path(self) -> pathlib.Path:  # pragma: no cover - shape parity only
        return self.root


# --------------------------------------------------------------------------- when


@when(parsers.parse("I select {count:d} rows from the shard"))
def _select(world: World, count: int) -> None:
    world.selection = run_select(
        reader=LocalShardReader(root=SHARDS),
        ref=SourceRef(),
        spec=SamplingSpec(limit=count),
        out_dir=world.tmp_path / "select",
    )


@when("I score the selected rows for agriculture relevance")
def _score(world: World) -> None:
    world.scoring = run_score(
        records=read_jsonl(world.selection.records_path), out_dir=world.tmp_path / "score"
    )


@when("I retrieve the documents for the relevant rows")
def _retrieve(world: World) -> None:
    scored = read_jsonl(world.scoring.scored_path)
    relevant = [row for row in scored if row["relevance"]["relevant"]]
    assert relevant, "the fixture shard must contain at least one relevant row"
    world.rows = relevant
    world.retrieval = run_retrieve(
        transport=world.transport,
        rows=relevant,
        source=world.selection.manifest["source"],
        out_dir=world.tmp_path / "retrieve",
    )


@when("I retrieve that row")
def _retrieve_one(world: World) -> None:
    world._transport = world._transport or FixtureTransport(responses={})
    world.retrieval = run_retrieve(
        transport=world.transport,
        rows=world.rows,
        source=SourceRef().as_dict(),
        out_dir=world.tmp_path / "retrieve",
    )


@when("I extract the images from the retrieved documents")
def _extract(world: World) -> None:
    if isinstance(world.retrieval, _Staged):
        records, root = world.retrieval.records, world.retrieval.root
    else:
        records = read_jsonl(world.retrieval.records_path)
        root = world.tmp_path / "retrieve"
    world.extraction = run_extract(
        extractor=PypdfImageExtractor(),
        records=records,
        pdf_root=root,
        out_dir=world.tmp_path / "extract",
    )


@when("I score that row")
def _score_one(world: World) -> None:
    world.relevance = score(str(world.rows[0]["text"]))


@when("the publication policy evaluates it")
def _decide(world: World) -> None:
    world.decision = policy.decide(world.rows[0], require_artifact_hash=True)


@when("I run the whole pipeline twice")
def _twice(world: World) -> None:
    world.selection = [_full_run(world, world.tmp_path / name) for name in ("a", "b")]


def _full_run(world: World, out: pathlib.Path) -> dict[str, bytes]:
    selection = run_select(
        reader=LocalShardReader(root=SHARDS),
        ref=SourceRef(),
        spec=SamplingSpec(limit=20),
        out_dir=out / "select",
    )
    scoring = run_score(records=read_jsonl(selection.records_path), out_dir=out / "score")
    relevant = [r for r in read_jsonl(scoring.scored_path) if r["relevance"]["relevant"]]
    retrieval = run_retrieve(
        transport=world.transport,
        rows=relevant,
        source=selection.manifest["source"],
        out_dir=out / "retrieve",
    )
    extraction = run_extract(
        extractor=PypdfImageExtractor(),
        records=read_jsonl(retrieval.records_path),
        pdf_root=out / "retrieve",
        out_dir=out / "extract",
    )
    return {
        "select": selection.manifest_path.read_bytes(),
        "score": scoring.manifest_path.read_bytes(),
        "retrieve": retrieval.manifest_path.read_bytes(),
        "extract": extraction.manifest_path.read_bytes(),
        "images": extraction.images_path.read_bytes(),
    }


# --------------------------------------------------------------------------- then


@then("some rows are relevant and some are not")
def _mixed(world: World) -> None:
    assert 0 < world.scoring.relevant < world.scoring.scored


@then("every relevant row has evidence naming the terms that matched")
def _evidence(world: World) -> None:
    for row in read_jsonl(world.scoring.scored_path):
        if row["relevance"]["relevant"]:
            assert row["relevance"]["matched_terms"], row


@then("every retrieved document is stored under its own content hash")
def _stored(world: World) -> None:
    assert world.retrieval.retrieved > 0
    for record in read_jsonl(world.retrieval.records_path):
        if not record["ok"]:
            continue
        stored = world.tmp_path / "retrieve" / record["path"]
        assert stored.is_file()
        assert sha256_hex(stored.read_bytes()) == record["sha256"]
        assert stored.read_bytes().startswith(PDF_MAGIC)


@then("every extracted image links back to its page and its document")
def _linked(world: World) -> None:
    rows = read_jsonl(world.extraction.images_path)
    assert rows, "the end-to-end run must produce at least one image"
    documents = {d["pdf_sha256"] for d in read_jsonl(world.extraction.documents_path)}
    for row in rows:
        assert row["pdf_sha256"] in documents
        assert row["document_row_id"]
        assert isinstance(row["page_index"], int)
        assert isinstance(row["image_index"], int)
        assert (world.tmp_path / "extract" / row["path"]).is_file()


@then("the publication policy refuses to republish any source bytes")
def _refused(world: World) -> None:
    for record in read_jsonl(world.retrieval.records_path):
        assert record["publication"]["disposition"] != "publish-artifact"


@then("both runs produce byte-identical manifests")
def _identical(world: World) -> None:
    first, second = world.selection
    assert first == second


@then("no request was made for an irrelevant row")
def _not_fetched(world: World) -> None:
    relevant_urls = {url_for(row) for row in world.rows}
    assert world.transport.requested
    assert set(world.transport.requested) <= relevant_urls


@then("no request is made at all")
def _no_request(world: World) -> None:
    assert world.transport.requested == []


@then(parsers.parse('the failure is recorded as "{reason}"'))
def _reason(world: World, reason: str) -> None:
    records = read_jsonl(world.retrieval.records_path)
    assert [record["reason"] for record in records] == [reason]


@then("no artifact is stored")
def _no_artifact(world: World) -> None:
    assert world.retrieval.retrieved == 0
    assert not (world.tmp_path / "retrieve" / "pdfs").exists()


@then("no image artifact is stored")
def _no_images_stored(world: World) -> None:
    assert world.extraction.images == 0
    assert not (world.tmp_path / "extract" / "images").exists()


@then("the document is recorded as failed with a reason")
def _failed(world: World) -> None:
    document = read_jsonl(world.extraction.documents_path)[0]
    assert document["ok"] is False
    assert document["error"]


@then("the document is recorded as a zero-image success")
def _zero(world: World) -> None:
    document = read_jsonl(world.extraction.documents_path)[0]
    assert document["ok"] is True
    assert document["image_count"] == 0
    assert world.extraction.failed == 0


@then("it is not relevant and the result has no evidence")
def _blank_result(world: World) -> None:
    assert not world.relevance.relevant
    assert world.relevance.evidence == {}


@then(parsers.parse('the decision is "{disposition}"'))
def _disposition(world: World, disposition: str) -> None:
    assert world.decision.as_dict()["disposition"] == disposition


@then("the provenance still traces back to the FinePDFs row")
def _traceable(world: World) -> None:
    assert policy.missing_provenance(world.rows[0]) == []
