"""Fetching bytes from third-party servers, under the bounds the domain defines.

This module moves bytes and nothing else. Whether a URL may be requested, whether a response is a
PDF, and where its bytes belong are all decided in :mod:`finepdf_to_images.domain.retrieval`.

The transport is a protocol so tests never touch the network. Required CI contacts no third-party
site at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from finepdf_to_images.domain.retrieval import (
    FailureReason,
    RetrievalLimits,
    SafeUrl,
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
        self.requested.append(url.url)
        if url.url in self.errors:
            raise self.errors[url.url]
        try:
            response = self.responses[url.url]
        except KeyError as error:
            raise AssertionError(f"no fixture registered for {url.url}") from error
        if len(response.body) > limits.max_bytes:
            # Mirror the real transport: stop at the bound and mark the body incomplete.
            return Response(
                status=response.status,
                content_type=response.content_type,
                body=response.body[: limits.max_bytes + 1],
                truncated=True,
            )
        return response


class HttpxTransport:
    """The real transport. Streams, and stops reading at the byte limit.

    Streaming matters: checking the size after downloading is not a limit, it is a report. A
    server advertising a small ``Content-Length`` and sending gigabytes is an ordinary hazard of
    fetching from arbitrary hosts.
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
        last: Exception | None = None

        for attempt in range(attempts):
            try:
                return self._attempt(httpx, url, limits, timeout)
            except httpx.TooManyRedirects as error:
                raise TransportError(FailureReason.TOO_MANY_REDIRECTS, str(error)) from error
            except httpx.TimeoutException as error:
                # A timeout is retried once: it is a failure to get an answer, not an answer.
                last = error
            except httpx.HTTPError as error:
                last = error
            if attempt + 1 < attempts:
                time.sleep(0)  # yield; no backoff is warranted for a single bounded retry

        if isinstance(last, httpx.TimeoutException):
            raise TransportError(FailureReason.TIMEOUT, str(last))
        raise TransportError(FailureReason.TRANSPORT_ERROR, str(last))

    def _attempt(self, httpx: Any, url: SafeUrl, limits: RetrievalLimits, timeout: Any) -> Response:
        with (
            httpx.Client(
                timeout=timeout,
                follow_redirects=limits.max_redirects > 0,
                max_redirects=max(limits.max_redirects, 1),
                headers={"User-Agent": self.user_agent, "Accept": "application/pdf,*/*;q=0.5"},
            ) as client,
            client.stream("GET", url.url) as response,
        ):
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
            )
