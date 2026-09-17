"""The real HTTP transport, driven offline through httpx's own mock transport.

`HttpxTransport` is the piece that actually talks to third-party servers, so it is the piece most
worth testing and the hardest to reach without a network. `httpx.MockTransport` runs the genuine
client -- streaming, redirect handling, timeouts, header parsing -- against a callable instead of a
socket, so these are real exercises of the production code path and still contact nothing.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from finepdf_to_images.adapters.retrieval import HttpxTransport, Response, TransportError
from finepdf_to_images.domain.retrieval import (
    PDF_MAGIC,
    FailureReason,
    RetrievalLimits,
    validate_url,
)

PDF = PDF_MAGIC + b"1.7\n% fixture\n%%EOF\n"
START = "https://fixtures.invalid/a.pdf"


def fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_bytes: int = RetrievalLimits().max_bytes,
    max_redirects: int = RetrievalLimits().max_redirects,
    retries: int = RetrievalLimits().retries,
) -> Response:
    """Run the real HttpxTransport against ``handler`` instead of a socket."""
    transport = HttpxTransport(http_transport=httpx.MockTransport(handler))
    limits = RetrievalLimits(max_bytes=max_bytes, max_redirects=max_redirects, retries=retries)
    return transport.fetch(validate_url(START), limits)


def test_a_successful_response_is_returned_whole() -> None:
    response = fetch(
        lambda _: httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    assert response.status == 200
    assert response.body == PDF
    assert response.content_type == "application/pdf"
    assert response.truncated is False
    assert response.final_url == START


def test_the_byte_limit_stops_the_read_mid_stream() -> None:
    """The read stops at the first chunk that crosses the bound, and says the body is partial.

    Overshoot is bounded by one chunk, not by zero: the mock transport delivers the whole body at
    once, and a real server can do the same. That is the caveat documented alongside the
    decompression note -- the limit stops the *read*, it does not cap peak memory to the byte.
    """
    response = fetch(lambda _: httpx.Response(200, content=PDF_MAGIC + b"x" * 10_000), max_bytes=64)
    assert response.truncated is True


def test_a_safe_redirect_is_followed_to_the_target() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/a.pdf":
            return httpx.Response(302, headers={"location": "https://fixtures.invalid/b.pdf"})
        return httpx.Response(200, content=PDF)

    response = fetch(handler)
    assert seen == [START, "https://fixtures.invalid/b.pdf"]
    assert response.body == PDF
    assert response.final_url == "https://fixtures.invalid/b.pdf"


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/secret",
        "file:///etc/passwd",
        "http://2130706433/",
    ],
)
def test_an_unsafe_redirect_target_is_never_requested(location: str) -> None:
    """The real client must not follow these, which is why the chain is walked by hand."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(TransportError) as excinfo:
        fetch(handler)
    assert excinfo.value.reason == FailureReason.UNSAFE_REDIRECT
    assert seen == [START]


def test_a_chain_longer_than_the_limit_is_refused() -> None:
    hops: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(1)
        return httpx.Response(
            302, headers={"location": f"https://fixtures.invalid/hop{len(hops)}.pdf"}
        )

    with pytest.raises(TransportError) as excinfo:
        fetch(handler, max_redirects=2)
    assert excinfo.value.reason == FailureReason.TOO_MANY_REDIRECTS
    assert len(hops) == 3, "the original request plus exactly max_redirects hops"


def test_a_chain_within_the_limit_succeeds() -> None:
    hops: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(1)
        if len(hops) <= 2:
            return httpx.Response(
                302, headers={"location": f"https://fixtures.invalid/hop{len(hops)}.pdf"}
            )
        return httpx.Response(200, content=PDF)

    assert fetch(handler, max_redirects=2).body == PDF


def test_a_redirect_without_a_location_is_returned_as_its_status() -> None:
    response = fetch(lambda _: httpx.Response(302))
    assert response.status == 302


def test_a_timeout_is_retried_and_then_reported() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectTimeout("too slow")

    with pytest.raises(TransportError) as excinfo:
        fetch(handler, retries=1)
    assert excinfo.value.reason == FailureReason.TIMEOUT
    assert len(attempts) == 2, "a timeout is worth exactly one more attempt"


def test_a_connection_error_is_not_retried() -> None:
    """REGRESSION: a malformed request was retried once and was guaranteed to fail identically."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(TransportError) as excinfo:
        fetch(handler, retries=1)
    assert excinfo.value.reason == FailureReason.TRANSPORT_ERROR
    assert len(attempts) == 1


def test_an_unexpected_exception_becomes_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("something httpx never documented")

    with pytest.raises(TransportError) as excinfo:
        fetch(handler)
    assert excinfo.value.reason == FailureReason.TRANSPORT_ERROR
    assert "RuntimeError" in excinfo.value.detail


def test_the_user_agent_identifies_this_project() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=PDF)

    fetch(handler)
    assert "finepdf-to-images" in seen["user-agent"]
