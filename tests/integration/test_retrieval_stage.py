"""The ``retrieve`` stage. Offline: every response comes from a fixture transport.

Required CI contacts no third-party site, which is the point of the transport being a protocol.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from finepdf_to_images.adapters.retrieval import FixtureTransport, Response, TransportError
from finepdf_to_images.adapters.storage import read_jsonl
from finepdf_to_images.domain.retrieval import (
    PDF_MAGIC,
    FailureReason,
    RetrievalLimits,
)
from finepdf_to_images.domain.serialization import sha256_hex
from finepdf_to_images.pipeline import run_retrieve

pytestmark = pytest.mark.integration

PDF_A = PDF_MAGIC + b"1.7\n% fixture A\n%%EOF\n"
PDF_B = PDF_MAGIC + b"1.7\n% fixture B\n%%EOF\n"
HTML = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"

SOURCE = {
    "dataset": "HuggingFaceFW/finepdfs",
    "revision": "220bac3acbf07789502c621d2d33952f51ac7f86",
    "config": "eng_Latn",
    "split": "train",
    "shard": "000_00000.parquet",
}


def row(index: int, url: str) -> dict[str, object]:
    return {"row_index": index, "row_id": f"<urn:uuid:{index:012d}>", "url": url}


def pdf_response(body: bytes = PDF_A) -> Response:
    return Response(status=200, content_type="application/pdf", body=body)


def retrieve(
    rows: list[dict[str, object]],
    responses: dict[str, Response],
    tmp_path: pathlib.Path,
    *,
    errors: dict[str, TransportError] | None = None,
    limits: RetrievalLimits | None = None,
):
    transport = FixtureTransport(responses=responses, errors=errors or {})
    result = run_retrieve(
        transport=transport,
        rows=rows,
        source=SOURCE,
        out_dir=tmp_path,
        limits=limits,
    )
    return result, transport


# --------------------------------------------------------------------------- the happy path


def test_a_valid_pdf_records_bytes_hash_size_and_source(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/a.pdf"
    result, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path)
    assert result.retrieved == 1
    record = read_jsonl(result.records_path)[0]
    assert record["ok"] is True
    assert record["sha256"] == sha256_hex(PDF_A)
    assert record["byte_size"] == len(PDF_A)
    assert record["url"] == url
    assert record["status"] == 200
    assert record["path"].startswith("pdfs/")


def test_the_pdf_bytes_are_written_under_their_content_addressed_path(
    tmp_path: pathlib.Path,
) -> None:
    url = "https://fixtures.invalid/a.pdf"
    result, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path)
    stored = tmp_path / read_jsonl(result.records_path)[0]["path"]
    assert stored.is_file()
    assert stored.read_bytes() == PDF_A
    assert sha256_hex(stored.read_bytes()) == stored.stem


def test_a_failed_retrieval_writes_no_artifact(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/notfound.pdf"
    retrieve([row(0, url)], {url: pdf_response(HTML)}, tmp_path)
    assert not (tmp_path / "pdfs").exists()


def test_duplicate_bytes_are_stored_once_on_disk(tmp_path: pathlib.Path) -> None:
    urls = ["https://a.invalid/x.pdf", "https://b.invalid/y.pdf"]
    retrieve(
        [row(0, urls[0]), row(1, urls[1])],
        {url: pdf_response(PDF_A) for url in urls},
        tmp_path,
    )
    assert len(list((tmp_path / "pdfs").rglob("*.pdf"))) == 1


def test_the_manifest_carries_the_source_and_the_limits(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/a.pdf"
    result, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path)
    assert result.manifest["source"] == SOURCE
    assert result.manifest["limits"]["max_bytes"] == RetrievalLimits().max_bytes


# --------------------------------------------------------------------------- refusals


def test_a_non_http_url_is_never_requested(tmp_path: pathlib.Path) -> None:
    """Rejected before the network, which is why the transport records no request at all."""
    result, transport = retrieve([row(0, "ftp://fixtures.invalid/a.pdf")], {}, tmp_path)
    assert transport.requested == []
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == FailureReason.UNSAFE_URL
    assert record["sha256"] is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/a.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pw@fixtures.invalid/a.pdf",
        "",
    ],
)
def test_unsafe_urls_are_refused_without_a_request(url: str, tmp_path: pathlib.Path) -> None:
    result, transport = retrieve([row(0, url)], {}, tmp_path)
    assert transport.requested == []
    assert read_jsonl(result.records_path)[0]["reason"] == FailureReason.UNSAFE_URL


def test_html_served_as_a_pdf_publishes_nothing_and_records_why(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/notfound.pdf"
    result, _ = retrieve(
        [row(0, url)],
        {url: Response(status=200, content_type="application/pdf", body=HTML)},
        tmp_path,
    )
    assert result.retrieved == 0
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == FailureReason.NOT_PDF
    assert record["content_type"] == "application/pdf"
    assert record["sha256"] is None
    assert "%PDF-" in record["detail"]


def test_an_error_status_records_the_status(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/gone.pdf"
    result, _ = retrieve(
        [row(0, url)], {url: Response(status=503, content_type="text/html", body=HTML)}, tmp_path
    )
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == FailureReason.HTTP_STATUS
    assert record["status"] == 503


def test_a_response_over_the_byte_limit_stops_within_the_bound(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/huge.pdf"
    huge = PDF_MAGIC + b"x" * 10_000
    result, _ = retrieve(
        [row(0, url)], {url: pdf_response(huge)}, tmp_path, limits=RetrievalLimits(max_bytes=1024)
    )
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == FailureReason.TOO_LARGE
    assert record["sha256"] is None


@pytest.mark.parametrize(
    "reason",
    [FailureReason.TIMEOUT, FailureReason.TRANSPORT_ERROR, FailureReason.TOO_MANY_REDIRECTS],
)
def test_a_transport_failure_is_a_deterministic_record(
    reason: FailureReason, tmp_path: pathlib.Path
) -> None:
    url = "https://fixtures.invalid/slow.pdf"
    result, _ = retrieve(
        [row(0, url)], {}, tmp_path, errors={url: TransportError(reason, "fixture failure")}
    )
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == reason
    assert record["detail"] == "fixture failure"
    assert record["ok"] is False


def test_failures_are_counted_by_reason(tmp_path: pathlib.Path) -> None:
    rows = [row(0, "ftp://x.invalid/a.pdf"), row(1, "https://fixtures.invalid/b.pdf")]
    responses = {"https://fixtures.invalid/b.pdf": pdf_response(HTML)}
    result, _ = retrieve(rows, responses, tmp_path)
    assert result.manifest["failures"] == {"not-pdf": 1, "unsafe-url": 1}


# --------------------------------------------------------------------------- redirects


def redirect(location: str, status: int = 302) -> Response:
    return Response(status=status, content_type="text/html", body=b"", location=location)


def test_a_safe_redirect_is_followed_to_the_pdf(tmp_path: pathlib.Path) -> None:
    start, target = "https://a.invalid/go.pdf", "https://b.invalid/real.pdf"
    result, transport = retrieve(
        [row(0, start)], {start: redirect(target), target: pdf_response()}, tmp_path
    )
    assert transport.requested == [start, target]
    assert result.retrieved == 1


def test_a_redirect_to_the_metadata_endpoint_is_refused(tmp_path: pathlib.Path) -> None:
    """REGRESSION: httpx followed redirects without validate_url ever seeing the target, so an
    ordinary crawl URL answering 302 to 169.254.169.254 was fetched and its body hashed."""
    start = "https://perfectly-ordinary.invalid/paper.pdf"
    result, transport = retrieve(
        [row(0, start)], {start: redirect("http://169.254.169.254/latest/meta-data/")}, tmp_path
    )
    assert transport.requested == [start], "the redirect target must never be requested"
    record = read_jsonl(result.records_path)[0]
    assert record["reason"] == FailureReason.UNSAFE_REDIRECT
    assert record["sha256"] is None


@pytest.mark.parametrize(
    "location",
    ["http://127.0.0.1/x", "file:///etc/passwd", "ftp://x.invalid/a.pdf", "http://2130706433/"],
)
def test_every_unsafe_redirect_target_is_refused(location: str, tmp_path: pathlib.Path) -> None:
    start = "https://a.invalid/go.pdf"
    result, transport = retrieve([row(0, start)], {start: redirect(location)}, tmp_path)
    assert transport.requested == [start]
    assert read_jsonl(result.records_path)[0]["reason"] == FailureReason.UNSAFE_REDIRECT


def test_a_redirect_chain_longer_than_the_limit_is_refused(tmp_path: pathlib.Path) -> None:
    urls = [f"https://hop{i}.invalid/a.pdf" for i in range(6)]
    responses = {url: redirect(urls[i + 1]) for i, url in enumerate(urls[:-1])}
    responses[urls[-1]] = pdf_response()
    result, _ = retrieve(
        [row(0, urls[0])], responses, tmp_path, limits=RetrievalLimits(max_redirects=2)
    )
    assert read_jsonl(result.records_path)[0]["reason"] == FailureReason.TOO_MANY_REDIRECTS


def test_a_chain_within_the_limit_still_succeeds(tmp_path: pathlib.Path) -> None:
    urls = [f"https://hop{i}.invalid/a.pdf" for i in range(3)]
    responses = {url: redirect(urls[i + 1]) for i, url in enumerate(urls[:-1])}
    responses[urls[-1]] = pdf_response()
    result, _ = retrieve(
        [row(0, urls[0])], responses, tmp_path, limits=RetrievalLimits(max_redirects=2)
    )
    assert result.retrieved == 1


def test_the_record_says_where_the_bytes_actually_came_from(tmp_path: pathlib.Path) -> None:
    """After a redirect the original crawl URL alone cannot say where the artifact was served."""
    start, target = "https://a.invalid/go.pdf", "https://b.invalid/real.pdf"
    result, _ = retrieve(
        [row(0, start)], {start: redirect(target), target: pdf_response()}, tmp_path
    )
    record = read_jsonl(result.records_path)[0]
    assert record["url"] == start
    assert record["final_url"] == target


def test_an_oversized_redirect_response_still_follows_the_chain(tmp_path: pathlib.Path) -> None:
    """REGRESSION: the fixture transport dropped the Location when it truncated an oversized body,
    so a test would stop the chain where the real transport followed it."""
    start, target = "https://a.invalid/go.pdf", "https://b.invalid/real.pdf"
    bulky = Response(status=302, content_type="text/html", body=b"x" * 5000, location=target)
    result, transport = retrieve(
        [row(0, start)],
        {start: bulky, target: pdf_response()},
        tmp_path,
        limits=RetrievalLimits(max_bytes=1024),
    )
    assert transport.requested == [start, target]
    assert result.retrieved == 1


def test_a_relative_redirect_is_resolved_and_followed(tmp_path: pathlib.Path) -> None:
    start, target = "https://a.invalid/dir/go.pdf", "https://a.invalid/real.pdf"
    result, transport = retrieve(
        [row(0, start)], {start: redirect("../real.pdf"), target: pdf_response()}, tmp_path
    )
    assert transport.requested == [start, target]
    assert result.retrieved == 1


# --------------------------------------------------------------------------- robustness


def test_an_unexpected_transport_exception_becomes_a_record_not_a_crash(
    tmp_path: pathlib.Path,
) -> None:
    """REGRESSION: httpx.InvalidURL does not inherit from HTTPError, so one malformed row aborted
    the whole run -- losing every record already fetched, since the manifest is written last."""

    class ExplodingTransport:
        def fetch(self, url: object, limits: object) -> Response:
            raise RuntimeError("something the transport never anticipated")

    from finepdf_to_images.pipeline import run_retrieve as run

    result = run(
        transport=ExplodingTransport(),
        rows=[row(0, "https://a.invalid/x.pdf"), row(1, "https://b.invalid/y.pdf")],
        source=SOURCE,
        out_dir=tmp_path,
    )
    assert result.attempted == 2
    records = read_jsonl(result.records_path)
    assert all(r["reason"] == FailureReason.TRANSPORT_ERROR for r in records)
    assert "RuntimeError" in records[0]["detail"]


def test_one_bad_row_does_not_lose_the_records_already_fetched(tmp_path: pathlib.Path) -> None:
    good, bad = "https://good.invalid/a.pdf", "http://2130706433/x.pdf"
    result, _ = retrieve([row(0, good), row(1, bad)], {good: pdf_response()}, tmp_path)
    assert result.retrieved == 1
    assert result.attempted == 2


# --------------------------------------------------------------------------- deduplication


def test_identical_bytes_from_two_urls_are_one_artifact(tmp_path: pathlib.Path) -> None:
    """Deduplication is by content, not by URL."""
    urls = ["https://a.invalid/x.pdf", "https://b.invalid/y.pdf"]
    result, _ = retrieve(
        [row(0, urls[0]), row(1, urls[1])],
        {url: pdf_response(PDF_A) for url in urls},
        tmp_path,
    )
    assert result.retrieved == 2
    assert result.unique == 1
    first, second = read_jsonl(result.records_path)
    assert first["sha256"] == second["sha256"]
    assert first["duplicate_of"] is None
    assert second["duplicate_of"] == "0"


def test_different_bytes_stay_separate_artifacts(tmp_path: pathlib.Path) -> None:
    urls = ["https://a.invalid/x.pdf", "https://b.invalid/y.pdf"]
    result, _ = retrieve(
        [row(0, urls[0]), row(1, urls[1])],
        {urls[0]: pdf_response(PDF_A), urls[1]: pdf_response(PDF_B)},
        tmp_path,
    )
    assert result.unique == 2


def test_artifact_identity_never_changes_between_runs(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/a.pdf"
    first, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path / "a")
    second, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path / "b")
    assert first.records_path.read_bytes() == second.records_path.read_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()


# --------------------------------------------------------------------------- policy integration


def test_a_retrieved_artifact_is_metadata_only_by_default(tmp_path: pathlib.Path) -> None:
    """No allow-list entry exists, so the conservative policy refuses to republish the bytes."""
    url = "https://fixtures.invalid/a.pdf"
    result, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path)
    publication = read_jsonl(result.records_path)[0]["publication"]
    assert publication["disposition"] == "metadata-only"
    assert publication["license"]["status"] == "unknown"


def test_a_failed_retrieval_is_still_judged_by_the_policy(tmp_path: pathlib.Path) -> None:
    result, _ = retrieve([row(0, "ftp://x.invalid/a.pdf")], {}, tmp_path)
    assert read_jsonl(result.records_path)[0]["publication"]["disposition"] in {
        "metadata-only",
        "exclude",
    }


def test_no_run_publishes_bytes_without_a_curated_licence(tmp_path: pathlib.Path) -> None:
    urls = [f"https://fixtures.invalid/{i}.pdf" for i in range(5)]
    rows = [row(i, url) for i, url in enumerate(urls)]
    result, _ = retrieve(rows, {url: pdf_response() for url in urls}, tmp_path)
    for record in read_jsonl(result.records_path):
        assert record["publication"]["disposition"] != "publish-artifact"


# --------------------------------------------------------------------------- empty and ordering


def test_retrieving_nothing_is_an_empty_run_not_an_error(tmp_path: pathlib.Path) -> None:
    result, _ = retrieve([], {}, tmp_path)
    assert result.attempted == 0
    assert result.manifest["counts"] == {
        "attempted": 0,
        "retrieved": 0,
        "unique": 0,
        "failed": 0,
    }


def test_records_follow_the_input_order(tmp_path: pathlib.Path) -> None:
    urls = [f"https://fixtures.invalid/{i}.pdf" for i in range(4)]
    rows = [row(i, url) for i, url in enumerate(urls)]
    result, _ = retrieve(rows, {url: pdf_response() for url in urls}, tmp_path)
    assert [r["row_index"] for r in read_jsonl(result.records_path)] == [0, 1, 2, 3]


def test_the_manifest_is_valid_json_with_stable_keys(tmp_path: pathlib.Path) -> None:
    url = "https://fixtures.invalid/a.pdf"
    result, _ = retrieve([row(0, url)], {url: pdf_response()}, tmp_path)
    parsed = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert set(parsed) == {
        "schema_version",
        "stage",
        "source",
        "limits",
        "counts",
        "failures",
        "records_digest",
    }


# --------------------------------------------------------------------------- CLI


def run_cli(args: list[str]) -> int:
    from finepdf_to_images.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def test_cli_rejects_the_wrong_manifest_with_a_diagnostic(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION: pointing --select-manifest at the score manifest -- an easy mistake -- printed
    a bare KeyError traceback."""
    from finepdf_to_images.cli import EXIT_FAILURE

    wrong = tmp_path / "manifest.json"
    wrong.write_text('{"stage":"score","counts":{}}', encoding="utf-8")
    scored = tmp_path / "scored.jsonl"
    scored.write_text("", encoding="utf-8")
    code = run_cli(
        [
            "retrieve",
            "--scored",
            str(scored),
            "--select-manifest",
            str(wrong),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == EXIT_FAILURE
    assert "not a select manifest" in capsys.readouterr().err


def test_cli_rejects_a_manifest_without_a_source_block(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from finepdf_to_images.cli import EXIT_FAILURE

    wrong = tmp_path / "manifest.json"
    wrong.write_text('{"stage":"select","source":"not-a-mapping"}', encoding="utf-8")
    scored = tmp_path / "scored.jsonl"
    scored.write_text("", encoding="utf-8")
    code = run_cli(
        [
            "retrieve",
            "--scored",
            str(scored),
            "--select-manifest",
            str(wrong),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == EXIT_FAILURE
    assert "no usable 'source' block" in capsys.readouterr().err
