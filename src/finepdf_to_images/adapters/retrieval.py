"""Fetching bytes from third-party servers, under the bounds the domain defines.

This module moves bytes and nothing else. Whether a URL may be requested, whether a response is a
PDF, and where its bytes belong are all decided in :mod:`finepdf_to_images.domain.retrieval`.

The transport is a protocol so tests never touch the network. Required CI contacts no third-party
site at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from typing import Any, Protocol

from finepdf_to_images.domain.retrieval import (
    REDIRECT_STATUSES,
    FailureReason,
    RetrievalLimits,
    SafeUrl,
    UnsafeUrlError,
    next_hop,
)


class TransportError(Exception):
    """A request that produced no response, carrying the domain's reason for it."""

    def __init__(self, reason: FailureReason, detail: str = "") -> None:
        super().__init__(detail or str(reason))
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Response:
    """What a transport returns: a status, a content type, and bounded bytes."""

    status: int
    content_type: str
    body: bytes
    #: True when the transport stopped reading because the body exceeded ``max_bytes``. The body
    #: is then deliberately incomplete, and the domain records it as a size failure.
    truncated: bool = False
    #: The ``Location`` header, when the status is a redirect. Present so fixtures can exercise the
    #: redirect chain without a network.
    location: str = ""
    #: The URL this response actually came from, which differs from the requested one after a
    #: redirect. Without it the manifest cannot say where the bytes were served from.
    final_url: str = ""


class Transport(Protocol):
    """Fetches one URL under ``limits``, or raises :class:`TransportError`."""

    def fetch(self, url: SafeUrl, limits: RetrievalLimits) -> Response: ...


@dataclass(frozen=True, slots=True)
class FixtureTransport:
    """Serves canned responses by URL. The only transport the tests use.

    An unknown URL raises rather than returning a 404, so a test that forgets to register a
    fixture fails loudly instead of silently exercising the not-found path.
    """

    responses: dict[str, Response]
    errors: dict[str, TransportError] = field(default_factory=dict)
    #: Records every URL actually requested, so tests can assert what was *not* fetched.
    requested: list[str] = field(default_factory=list)

    def fetch(self, url: SafeUrl, limits: RetrievalLimits) -> Response:
        current = url
        for hop in range(limits.max_redirects + 1):
            response = self._one(current, limits)
            if response.status not in REDIRECT_STATUSES or not response.location:
                return response
            if hop == limits.max_redirects:
                break
            try:
                current = next_hop(current, response.location)
            except UnsafeUrlError as error:
                raise TransportError(FailureReason.UNSAFE_REDIRECT, str(error)) from error
        raise TransportError(
            FailureReason.TOO_MANY_REDIRECTS, f"more than {limits.max_redirects} redirects"
        )

    def _one(self, url: SafeUrl, limits: RetrievalLimits) -> Response:
        self.requested.append(url.url)
        if url.url in self.errors:
            raise self.errors[url.url]
        try:
            response = self.responses[url.url]
        except KeyError as error:
            raise AssertionError(f"no fixture registered for {url.url}") from error
        if len(response.body) > limits.max_bytes:
            # Mirror the real transport: stop at the bound and mark the body incomplete. The
            # location must survive, or an oversized 3xx would stop the chain here while the real
            # transport followed it -- a fixture/real divergence that hides bugs.
            return dataclass_replace(
                response,
                body=response.body[: limits.max_bytes + 1],
                truncated=True,
                final_url=url.url,
            )
        return dataclass_replace(response, final_url=url.url)


class HttpxTransport:
    """The real transport. Streams, stops at the byte limit, and validates every redirect hop.

    Streaming matters: checking the size after downloading is not a limit, it is a report. A
    server advertising a small ``Content-Length`` and sending gigabytes is an ordinary hazard of
    fetching from arbitrary hosts.

    Redirects are driven by hand rather than by ``follow_redirects=True``, because the library
    would follow them without ever showing them to :func:`validate_url`. A crawl URL answering
    ``302 Location: http://169.254.169.254/`` must not be followed, and letting httpx decide is
    exactly how that happens.
    """

    def __init__(self, user_agent: str = "finepdf-to-images/0.1 (+research POC)") -> None:
        self.user_agent = user_agent

    def fetch(self, url: SafeUrl, limits: RetrievalLimits) -> Response:
        import httpx

        # httpx requires a default or all four parameters; write and pool follow the read bound.
        timeout = httpx.Timeout(
            limits.read_timeout,
            connect=limits.connect_timeout,
            read=limits.read_timeout,
        )
        attempts = limits.retries + 1
        last: Exception = TransportError(FailureReason.TIMEOUT, "no attempt was made")

        for _attempt in range(attempts):
            try:
                return self._follow(httpx, url, limits, timeout)
            except TransportError:
                # Already classified by the domain -- an unsafe redirect or too many hops is a
                # decision, not a transient fault, so retrying it would be pointless.
                raise
            except httpx.TimeoutException as error:
                # A timeout is retried once: it is a failure to get an answer, not an answer.
                last = error
            except Exception as error:
                # Deliberately broad. httpx.InvalidURL and friends do not inherit from HTTPError,
                # and one malformed row escaping here aborted an entire run -- losing every record
                # already fetched, because the manifest is written after the loop. A failure must
                # be a record, not an exception.
                #
                # Not retried: a malformed URL will fail identically the second time. Only a
                # timeout is worth another attempt.
                raise TransportError(
                    FailureReason.TRANSPORT_ERROR, f"{type(error).__name__}: {error}"
                ) from error

        # Only a timeout reaches here: every other exception is classified and raised inside the
        # loop, because retrying it would fail identically.
        raise TransportError(FailureReason.TIMEOUT, str(last))

    def _follow(self, httpx: Any, url: SafeUrl, limits: RetrievalLimits, timeout: Any) -> Response:
        """Walk the redirect chain by hand, validating every hop."""
        current = url
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": self.user_agent, "Accept": "application/pdf,*/*;q=0.5"},
        ) as client:
            for hop in range(limits.max_redirects + 1):
                response = self._stream(client, current, limits)
                if response.status not in REDIRECT_STATUSES or not response.location:
                    return response
                if hop == limits.max_redirects:
                    break
                try:
                    current = next_hop(current, response.location)
                except UnsafeUrlError as error:
                    raise TransportError(FailureReason.UNSAFE_REDIRECT, str(error)) from error
        raise TransportError(
            FailureReason.TOO_MANY_REDIRECTS, f"more than {limits.max_redirects} redirects"
        )

    def _stream(self, client: Any, url: SafeUrl, limits: RetrievalLimits) -> Response:
        with client.stream("GET", url.url) as response:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > limits.max_bytes:
                    truncated = True
                    break
            return Response(
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                body=b"".join(chunks),
                truncated=truncated,
                location=response.headers.get("location", ""),
                final_url=url.url,
            )
