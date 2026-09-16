# Retrieving source PDFs

This is the only stage that talks to arbitrary third-party servers, so most of its design is about
what it refuses to do.

```bash
uv run finepdf-to-images retrieve \
  --scored out/score/scored.jsonl \
  --select-manifest out/select/manifest.json \
  --relevant-only \
  --out out/retrieve
```

## What is refused, before any request

`validate_url` runs in the pure domain and raises **before** the network is touched. The tests
assert the transport recorded *no request at all* for each of these:

| refused | why |
| --- | --- |
| any scheme but `http`/`https` | `file://` reads the local disk, `data:` is not a fetch. The FinePDFs rows really do contain `ftp://` URLs. |
| URLs carrying credentials | `https://user:pw@host/` would send them to a third party and then into a manifest. Refused outright, not stripped. |
| `localhost`, loopback, private, link-local, reserved, multicast | A crawl-sourced URL pointing at *us* is not a document. Following it turns this pipeline into a request forwarder into whatever network it runs in. `169.254.169.254` is the cloud metadata endpoint. |
| malformed or hostless URLs | usually means the URL was never parseable |
| surrounding whitespace | refused rather than trimmed, consistently with the rest of the project |
| numeric or single-label hosts | `ipaddress` only parses canonical dotted quads, so `http://2130706433/`, `http://127.1/`, `http://0x7f000001/` and `http://0/` looked like ordinary hostnames — and the resolver turned every one back into `127.0.0.1`. A real public hostname has at least one dot and a final label starting with a letter. Punycode still passes. |
| control characters | `urlsplit` silently strips tab, CR and LF; httpx does not. Two parsers disagreeing about what is being requested is a bypass by construction. |

### Redirects are validated too

**Every hop**, not just the first. Redirects are walked by hand rather than handed to
`follow_redirects=True`, because the library would follow them without ever showing the target to
`validate_url`. A perfectly ordinary crawl URL answering

```
302 Location: http://169.254.169.254/latest/meta-data/
```

would otherwise be followed to the cloud metadata endpoint and its body hashed and recorded as a
successful retrieval. An unsafe hop is recorded as `unsafe-redirect`, and the tests assert the
target was **never requested**.

## Bounds

| | default | why |
| --- | --- | --- |
| connect timeout | 5 s | fail fast; a hundred documents from arbitrary servers should not wait out the slowest |
| read timeout | 15 s | |
| max bytes | 25 MB | an unbounded read from an untrusted server is how a small run fills a disk |
| max redirects | 3 | |
| retries | 1 | for a transient connection failure only — **never** for an HTTP status, which is an answer rather than a failure to get one |

The byte limit is enforced **while streaming**. Checking the size after downloading is not a limit,
it is a report: a server advertising a small `Content-Length` and sending gigabytes is an ordinary
hazard of fetching from arbitrary hosts.

The cap applies to *decompressed* bytes, which is the right place for it, but a small gzip chunk
can decode into a large one — so peak memory can exceed `max_bytes` by a single chunk before the
read stops. Acceptable at 25 MB; worth knowing before raising the limit.

No transport exception aborts a run. `httpx.InvalidURL` and friends do not inherit from
`HTTPError`, and one malformed row used to lose every record already fetched, because the manifest
is written after the loop. Anything unexpected becomes a `transport-error` record.

## What counts as a PDF

**The magic bytes, not the content type.** A server returning an HTML "not found" page with
`Content-Type: application/pdf` is common on the open web, and believing the header is how a
dataset acquires a thousand HTML files named `.pdf`. The content type is recorded as a diagnostic
and never trusted as proof.

Status is checked **before** the body: an error page that happens to start with `%PDF-` is still an
error page.

## Storage and identity

Successful PDFs are stored at `pdfs/<aa>/<bb>/<sha256>.pdf`, sharded so no directory grows without
bound. **Deduplication is by content, not by URL**: the same PDF served from two addresses is
written once, and the second row records `duplicate_of` instead of a second copy. The path is
derived from the content, so re-running cannot produce a second copy under a different name.

## Failures are results

Every failure is recorded with the same care as a success, with a reason from a closed set:
`unsafe-url`, `http-status`, `not-pdf`, `empty-body`, `too-large`, `timeout`,
`too-many-redirects`, `transport-error`. The manifest carries a count per reason. "We tried this
URL and got HTML" is something the run should be able to show, not a silence.

## Publication

Every record — successful or not — carries the verdict of
[`policy.decide()`](policy.md), with `require_artifact_hash=True` for retrieved bytes. Since the
pilot ships no curated allow-list entries, every artifact resolves to `metadata-only`: the hash and
provenance are published, the bytes are not. A test asserts that no run reaches
`publish-artifact`.

Ownership and licensing of the source documents are entirely the [policy's](policy.md) business,
not this stage's.

## Testing

The transport is a `Protocol`. Tests use `FixtureTransport`, which serves canned responses and
records every URL requested, so a test can assert what was **not** fetched. An unregistered URL
raises rather than returning a 404, so a test that forgets a fixture fails loudly instead of
silently exercising the not-found path.

**Required CI contacts no third-party site.**
