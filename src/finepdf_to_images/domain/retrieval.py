"""What may be fetched, what counts as a PDF, and where it is stored.

Pure. Every decision about a retrieval — is this URL safe to request, is this response actually a
PDF, is it too big, where do its bytes live — is made here, over plain values. The adapter only
moves bytes.

That split matters more here than anywhere else in the pipeline: this is the one stage that talks
to arbitrary third-party servers, and the rules for *not* doing something dangerous should be
readable and testable without a network.
"""

from __future__ import annotations

import dataclasses
import enum
import ipaddress
import re
from typing import Any
from urllib.parse import SplitResult, urlsplit

from finepdf_to_images.domain.serialization import sha256_hex

#: Every PDF starts with this. A server claiming ``application/pdf`` over an HTML error page is
#: common enough that the content type is recorded as a diagnostic and never trusted as proof.
PDF_MAGIC = b"%PDF-"

#: Schemes we are willing to request. Nothing else — not ``file``, not ``ftp``, not ``data``.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Hostnames that name the machine running the pipeline. Requesting these from a URL that came out
#: of a web crawl is never intentional.
_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

_HOST_RE = re.compile(
    r"\A[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\Z"
)


class UnsafeUrlError(ValueError):
    """A URL that must not be requested. Raised *before* any network access."""


class FailureReason(enum.StrEnum):
    """Why a retrieval produced no artifact. Recorded, never swallowed."""

    UNSAFE_URL = "unsafe-url"
    HTTP_STATUS = "http-status"
    NOT_PDF = "not-pdf"
    EMPTY_BODY = "empty-body"
    TOO_LARGE = "too-large"
    TIMEOUT = "timeout"
    TOO_MANY_REDIRECTS = "too-many-redirects"
    TRANSPORT_ERROR = "transport-error"


@dataclasses.dataclass(frozen=True, slots=True)
class RetrievalLimits:
    """The bounds every request runs under.

    Defaults are small on purpose. A proof of concept fetching a hundred documents from arbitrary
    servers should fail fast and cheaply rather than wait out a slow one.
    """

    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    #: 25 MB. Larger PDFs exist; this pilot does not need them, and an unbounded read from an
    #: untrusted server is how a small run fills a disk.
    max_bytes: int = 25_000_000
    max_redirects: int = 3
    #: One retry, for a transient connection failure only — never for an HTTP status, which is an
    #: answer rather than a failure to get one.
    retries: int = 1

    def __post_init__(self) -> None:
        for field in ("connect_timeout", "read_timeout"):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} must be positive")
        if self.max_bytes < len(PDF_MAGIC):
            raise ValueError("max_bytes must at least allow a PDF header")
        if self.max_redirects < 0 or self.retries < 0:
            raise ValueError("max_redirects and retries must not be negative")

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class SafeUrl:
    """A URL that passed every check in :func:`validate_url`."""

    url: str
    scheme: str
    host: str
    port: int | None


def validate_url(raw: str) -> SafeUrl:
    """Reject anything we are not willing to request, before any network access.

    The checks, and why each one is here:

    - **scheme** — only http and https. ``file://`` reads the local disk, ``data:`` is not a
      fetch at all, and the FinePDFs rows contain at least one ``ftp://`` URL.
    - **credentials** — a URL carrying a username or password would send them to a third-party
      server and then into a manifest. Refused outright rather than stripped.
    - **host shape** — a host must look like a hostname or an IP literal; an empty or malformed
      one usually means the URL was never parseable.
    - **local and private addresses** — a crawl-sourced URL naming localhost, a loopback, a
      link-local or a private-range address is pointing at *us*, not at a document. Following it
      would turn this pipeline into a request forwarder into whatever network it runs in.
    """
    parts = _split(raw)
    scheme = _checked_scheme(parts.scheme, raw)
    if parts.username or parts.password:
        raise UnsafeUrlError("url carries credentials; refusing to send them to a third party")
    host = _checked_host(parts.hostname, raw)
    return SafeUrl(url=raw, scheme=scheme, host=host, port=_checked_port(parts, raw))


def _split(raw: str) -> SplitResult:
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafeUrlError("empty url")
    if raw != raw.strip():
        raise UnsafeUrlError(f"url has surrounding whitespace: {raw!r}")
    try:
        return urlsplit(raw)
    except ValueError as error:
        raise UnsafeUrlError(f"unparseable url {raw!r}: {error}") from error


def _checked_scheme(scheme: str, raw: str) -> str:
    lowered = scheme.lower()
    if lowered not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme {lowered or '(none)'!r} in {raw!r}")
    return lowered


def _checked_host(hostname: str | None, raw: str) -> str:
    host = (hostname or "").lower()
    if not host:
        raise UnsafeUrlError(f"url has no host: {raw!r}")
    if host in _LOCAL_HOSTNAMES:
        raise UnsafeUrlError(f"url names the local machine: {host!r}")
    _reject_private_address(host, raw)
    if not _is_ip_literal(host) and not _HOST_RE.fullmatch(host):
        raise UnsafeUrlError(f"malformed host {host!r} in {raw!r}")
    return host


def _checked_port(parts: SplitResult, raw: str) -> int | None:
    try:
        return parts.port
    except ValueError as error:
        raise UnsafeUrlError(f"invalid port in {raw!r}") from error


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _reject_private_address(host: str, raw: str) -> None:
    """Refuse an IP literal that points inside our own network.

    Only literals can be checked purely; a hostname resolving to a private address is the
    adapter's problem, because resolving it is I/O. See TD-006.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise UnsafeUrlError(f"url points at a non-public address {host!r}: {raw!r}")


def looks_like_pdf(data: bytes) -> bool:
    """Whether ``data`` begins with the PDF header.

    Deliberately checked on the bytes rather than on the content type: a server returning an HTML
    "not found" page with ``Content-Type: application/pdf`` is an ordinary occurrence on the open
    web, and believing the header is how a dataset acquires a thousand HTML files named ``.pdf``.
    """
    return data.startswith(PDF_MAGIC)


def artifact_path(digest: str) -> str:
    """Content-addressed path for a PDF, sharded so no directory grows without bound."""
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise ValueError(f"not a sha-256 hex digest: {digest!r}")
    return f"pdfs/{digest[:2]}/{digest[2:4]}/{digest}.pdf"


@dataclasses.dataclass(frozen=True, slots=True)
class RetrievalRecord:
    """The complete outcome of one attempted retrieval, successful or not.

    A failure is recorded with the same care as a success. "We tried this URL and got HTML" is a
    result the run should be able to show, not a silence.
    """

    row_index: int
    row_id: str
    url: str
    ok: bool
    reason: str | None = None
    detail: str = ""
    status: int | None = None
    content_type: str = ""
    byte_size: int | None = None
    sha256: str | None = None
    path: str | None = None
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def evaluate(
    *,
    row_index: int,
    row_id: str,
    url: str,
    status: int,
    content_type: str,
    body: bytes,
    limits: RetrievalLimits,
) -> RetrievalRecord:
    """Turn a completed response into a success or a recorded failure.

    Order matters: a non-2xx status is reported as a status failure even if the body happens to
    start with ``%PDF-``, because an error page that looks like a PDF is still an error page.
    """
    base = RetrievalRecord(
        row_index=row_index,
        row_id=row_id,
        url=url,
        ok=False,
        status=status,
        content_type=content_type,
    )
    if not 200 <= status < 300:
        return dataclasses.replace(base, reason=FailureReason.HTTP_STATUS, detail=f"http {status}")
    if not body:
        return dataclasses.replace(base, reason=FailureReason.EMPTY_BODY)
    if len(body) > limits.max_bytes:
        return dataclasses.replace(
            base,
            reason=FailureReason.TOO_LARGE,
            detail=f"{len(body)} bytes exceeds the {limits.max_bytes} byte limit",
            byte_size=len(body),
        )
    if not looks_like_pdf(body):
        return dataclasses.replace(
            base,
            reason=FailureReason.NOT_PDF,
            detail=f"body begins {body[:8]!r}, not {PDF_MAGIC!r}",
            byte_size=len(body),
        )

    digest = sha256_hex(body)
    return dataclasses.replace(
        base, ok=True, byte_size=len(body), sha256=digest, path=artifact_path(digest)
    )


def failure(
    *, row_index: int, row_id: str, url: str, reason: FailureReason, detail: str = ""
) -> RetrievalRecord:
    """A retrieval that never produced a response at all."""
    return RetrievalRecord(
        row_index=row_index, row_id=row_id, url=url, ok=False, reason=reason, detail=detail
    )
