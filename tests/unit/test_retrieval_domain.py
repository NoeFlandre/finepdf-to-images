"""URL safety, PDF validation and retrieval outcomes.

This is the one stage that talks to arbitrary third-party servers, so most of this file is about
what must *not* happen.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finepdf_to_images.domain.retrieval import (
    PDF_MAGIC,
    FailureReason,
    RetrievalLimits,
    RetrievalRecord,
    UnsafeUrlError,
    artifact_path,
    evaluate,
    looks_like_pdf,
    validate_url,
)
from finepdf_to_images.domain.serialization import sha256_hex

PDF = PDF_MAGIC + b"1.7\n%fake fixture pdf\n"
LIMITS = RetrievalLimits()


def outcome(
    *,
    status: int = 200,
    content_type: str = "application/pdf",
    body: bytes = PDF,
    limits: RetrievalLimits = LIMITS,
    url: str = "https://example.invalid/a.pdf",
) -> RetrievalRecord:
    return evaluate(
        row_index=0,
        row_id="<urn:uuid:a>",
        url=url,
        status=status,
        content_type=content_type,
        body=body,
        limits=limits,
    )


# --------------------------------------------------------------------------- URL safety


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/a.pdf",
        "http://example.invalid/a.pdf",
        "HTTPS://Example.Invalid/A.pdf",
        "https://example.invalid:8443/a.pdf",
        "https://sub.domain.example.invalid/deep/path.pdf?x=1#frag",
        "https://93.184.216.34/a.pdf",
    ],
)
def test_ordinary_public_urls_are_accepted(url: str) -> None:
    assert validate_url(url).scheme in {"http", "https"}


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.invalid/a.pdf",
        "file:///etc/passwd",
        "data:application/pdf;base64,AAAA",
        "javascript:alert(1)",
        "gopher://example.invalid/a",
        "//example.invalid/a.pdf",
        "/etc/passwd",
        "example.invalid/a.pdf",
    ],
)
def test_unsupported_schemes_and_local_paths_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.invalid/a.pdf",
        "https://user@example.invalid/a.pdf",
        "http://admin:hunter2@example.invalid/a.pdf",
    ],
)
def test_urls_carrying_credentials_are_refused(url: str) -> None:
    """Refused outright rather than stripped: they would be sent to a third party, then logged."""
    with pytest.raises(UnsafeUrlError, match="credentials"):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/a.pdf",
        "http://localhost:8080/a.pdf",
        "http://127.0.0.1/a.pdf",
        "http://[::1]/a.pdf",
        "http://10.0.0.1/a.pdf",
        "http://192.168.1.1/a.pdf",
        "http://172.16.0.1/a.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/a.pdf",
        "http://[fd00::1]/a.pdf",
    ],
)
def test_addresses_inside_our_own_network_are_refused(url: str) -> None:
    """A crawl-sourced URL pointing at us is not a document; following it forwards requests
    into whatever network this runs in. 169.254.169.254 is the cloud metadata endpoint."""
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize("url", ["", "   ", "https://", "https:///a.pdf", "http://:80/a"])
def test_empty_or_hostless_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize(
    "url", [" https://example.invalid/a.pdf", "https://example.invalid/a.pdf "]
)
def test_surrounding_whitespace_is_refused_not_trimmed(url: str) -> None:
    with pytest.raises(UnsafeUrlError, match="whitespace"):
        validate_url(url)


def test_a_non_string_url_is_refused() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(None)  # ty: ignore[invalid-argument-type]


@given(raw=st.text(max_size=80))
def test_validating_arbitrary_text_either_succeeds_or_raises_unsafe(raw: str) -> None:
    """No input may produce an unexpected exception type: every rejection is a recorded one."""
    try:
        result = validate_url(raw)
    except UnsafeUrlError:
        return
    assert result.scheme in {"http", "https"}
    assert result.host


# --------------------------------------------------------------------------- PDF validation


def test_pdf_magic_bytes_are_recognised() -> None:
    assert looks_like_pdf(PDF)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<!DOCTYPE html><html>404</html>",
        b"%PD",
        b"\x00%PDF-1.7",
        b" %PDF-1.7",
        b"PK\x03\x04",
        PDF_MAGIC.lower(),
    ],
)
def test_anything_not_starting_with_the_header_is_not_a_pdf(body: bytes) -> None:
    assert not looks_like_pdf(body)


# --------------------------------------------------------------------------- limits


def test_limit_defaults_are_small() -> None:
    assert LIMITS.max_bytes == 25_000_000
    assert LIMITS.connect_timeout <= 10
    assert LIMITS.read_timeout <= 30


@pytest.mark.parametrize(
    "build",
    [
        lambda: RetrievalLimits(connect_timeout=0.0),
        lambda: RetrievalLimits(connect_timeout=-1.0),
        lambda: RetrievalLimits(read_timeout=0.0),
        lambda: RetrievalLimits(max_bytes=0),
        lambda: RetrievalLimits(max_bytes=3),
        lambda: RetrievalLimits(max_redirects=-1),
        lambda: RetrievalLimits(retries=-1),
    ],
    ids=[
        "connect0",
        "connect-neg",
        "read0",
        "bytes0",
        "bytes-tiny",
        "redirects-neg",
        "retries-neg",
    ],
)
def test_incoherent_limits_are_refused(build: Callable[[], RetrievalLimits]) -> None:
    with pytest.raises(ValueError):
        build()


# --------------------------------------------------------------------------- outcomes


def test_a_valid_pdf_response_records_hash_size_and_path() -> None:
    record = outcome()
    assert record.ok
    assert record.byte_size == len(PDF)
    assert record.sha256 == sha256_hex(PDF)
    assert record.path == artifact_path(sha256_hex(PDF))
    assert record.reason is None


@pytest.mark.parametrize("status", [301, 400, 403, 404, 500, 503])
def test_a_non_success_status_produces_no_artifact(status: int) -> None:
    record = outcome(status=status)
    assert not record.ok
    assert record.reason == FailureReason.HTTP_STATUS
    assert record.sha256 is None


def test_an_error_page_that_happens_to_look_like_a_pdf_is_still_an_error() -> None:
    """Order matters: status is checked before the body."""
    assert outcome(status=404, body=PDF).reason == FailureReason.HTTP_STATUS


def test_html_served_as_application_pdf_is_rejected() -> None:
    """Common on the open web, and the reason the content type is a diagnostic, not proof."""
    record = outcome(body=b"<!DOCTYPE html><html>Not found</html>")
    assert not record.ok
    assert record.reason == FailureReason.NOT_PDF
    assert record.content_type == "application/pdf"
    assert record.sha256 is None


def test_an_empty_body_is_its_own_reason() -> None:
    assert outcome(body=b"").reason == FailureReason.EMPTY_BODY


def test_a_body_over_the_limit_is_rejected_with_its_size() -> None:
    limits = RetrievalLimits(max_bytes=32)
    record = outcome(body=PDF_MAGIC + b"x" * 100, limits=limits)
    assert record.reason == FailureReason.TOO_LARGE
    assert record.byte_size == len(PDF_MAGIC) + 100
    assert record.sha256 is None


def test_a_body_exactly_at_the_limit_is_accepted() -> None:
    body = PDF_MAGIC + b"x" * 10
    assert outcome(body=body, limits=RetrievalLimits(max_bytes=len(body))).ok


def test_every_failure_records_a_reason_and_no_artifact() -> None:
    for record in (outcome(status=500), outcome(body=b"x"), outcome(body=b"")):
        assert record.reason
        assert record.sha256 is None and record.path is None


def test_outcomes_are_deterministic() -> None:
    assert outcome().as_dict() == outcome().as_dict()


def test_identical_bytes_always_produce_the_same_identity() -> None:
    """Deduplication depends on this: the same PDF from two URLs is one artifact."""
    a = outcome(url="https://a.invalid/x.pdf")
    b = outcome(url="https://b.invalid/y.pdf")
    assert a.sha256 == b.sha256
    assert a.path == b.path


# --------------------------------------------------------------------------- artifact paths


def test_artifact_path_is_content_addressed_and_sharded() -> None:
    digest = sha256_hex(b"x")
    assert artifact_path(digest) == f"pdfs/{digest[:2]}/{digest[2:4]}/{digest}.pdf"


@pytest.mark.parametrize(
    "digest", ["", "abc", "g" * 64, sha256_hex(b"x").upper(), sha256_hex(b"x") + "0"]
)
def test_artifact_path_refuses_anything_that_is_not_a_digest(digest: str) -> None:
    with pytest.raises(ValueError):
        artifact_path(digest)


@given(data=st.binary(max_size=200))
def test_artifact_paths_never_escape_their_prefix(data: bytes) -> None:
    path = artifact_path(sha256_hex(data))
    assert path.startswith("pdfs/")
    assert ".." not in path
