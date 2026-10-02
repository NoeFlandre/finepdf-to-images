# Retrieving source PDFs

This stage is the only stage that connects to arbitrary third-party servers. Thus most of its design is about what it refuses to do.

```bash
uv run finepdf-to-images retrieve \
  --scored out/score/scored.jsonl \
  --select-manifest out/select/manifest.json \
  --relevant-only \
  --out out/retrieve
```

## What the stage refuses before it sends a request

`validate_url` runs in the pure domain. It raises an error **before** the code touches the network. For each case below, the tests assert that the transport recorded *no request at all*.

| refused | reason |
| --- | --- |
| any scheme but `http` or `https` | `file://` reads the local disk. `data:` is not a fetch. The FinePDFs rows do contain `ftp://` URLs. |
| URLs that carry credentials | `https://user:pw@host/` sends the credentials to a third party and then puts them in a manifest. The stage refuses these URLs. It does not remove the credentials. |
| `localhost`, loopback, private, link-local, reserved, multicast | A crawl-sourced URL that points to *this system* is not a document. If the pipeline follows it, the pipeline becomes a request forwarder into the network where it runs. `169.254.169.254` is the cloud metadata endpoint. |
| malformed URLs or URLs without a host | Usually the URL was never parseable. |
| surrounding whitespace | The stage refuses the URL. It does not trim it. This is the same rule as in the rest of the project. |
| numeric or single-label hosts | `ipaddress` parses only canonical dotted quads. Thus `http://2130706433/`, `http://127.1/`, `http://0x7f000001/`, and `http://0/` looked like ordinary host names. The resolver changed each of them to `127.0.0.1`. A real public host name has at least one dot. Its last label starts with a letter. Punycode still passes. |
| control characters | `urlsplit` silently removes tab, CR, and LF. httpx does not. When two parsers disagree on the requested URL, a bypass is possible. |

### The stage also validates redirects

The stage validates **each hop**, not only the first. It follows redirects by hand. It does not give `follow_redirects=True` to the library. The library follows redirects and never shows the target to `validate_url`. Consider an ordinary crawl URL that answers:

```
302 Location: http://169.254.169.254/latest/meta-data/
```

The library follows this redirect to the cloud metadata endpoint. The pipeline hashes the body and records it as a successful retrieval. The stage records an unsafe hop as `unsafe-redirect`. The tests assert that the code **never requested** the target.

## Bounds

| | default | reason |
| --- | --- | --- |
| connect timeout | 5 s | Fail fast. A run of a hundred documents from arbitrary servers must not wait for the slowest server. |
| read timeout | 15 s | |
| max bytes | 25 MB | An unbounded read from an untrusted server can fill a disk with a small run. |
| max redirects | 3 | |
| retries | 1 | Only for a transient connection failure. **Never** for an HTTP status. An HTTP status is an answer and not a failure to get one. |

The stage enforces the byte limit **while it streams**. A check of the size after the download is a report and not a limit. A server can advertise a small `Content-Length` and send gigabytes. This is an ordinary hazard when you fetch from arbitrary hosts.

The cap applies to the *decompressed* bytes. This is the correct place. But a small gzip chunk can decode into a large chunk. Thus the peak memory can be larger than `max_bytes` by one chunk before the read stops. This is acceptable at 25 MB. Remember it before you increase the limit.

No transport exception aborts a run. `httpx.InvalidURL` and similar exceptions do not inherit from `HTTPError`. In the past, one malformed row caused the loss of each record that the stage already fetched. This is because the stage writes the manifest after the loop. Now each unexpected error becomes a `transport-error` record.

## What counts as a PDF

The stage uses **the magic bytes, not the content type**. On the open web, a server often returns an HTML "not found" page with `Content-Type: application/pdf`. If the stage believes the header, the dataset gets a thousand HTML files with the name `.pdf`. The stage records the content type as a diagnostic. It never uses it as proof.

The stage checks the status **before** it checks the body. An error page that starts with `%PDF-` is still an error page.

## Storage and identity

The stage stores each successful PDF at `pdfs/<aa>/<bb>/<sha256>.pdf`. The path is sharded, so no directory grows without bound. **The stage deduplicates by content and not by URL.** When two addresses serve the same PDF, the stage writes it once. The second row records `duplicate_of` and not a second copy. The path comes from the content. Thus a new run cannot make a second copy under a different name.

## Failures are results

The stage records each failure with the same care as a success. The reason comes from a closed set: `unsafe-url`, `http-status`, `not-pdf`, `empty-body`, `too-large`, `timeout`, `too-many-redirects`, `transport-error`. The manifest has a count for each reason. The run must be able to show "we tried this URL and got HTML". It must not stay silent.

## Publication

Each record, successful or not, carries the verdict of [`policy.decide()`](policy.md). For retrieved bytes, the stage sets `require_artifact_hash=True`. The pilot has no curated allow-list entries. Thus each artifact resolves to `metadata-only`. The pipeline publishes the hash and the provenance. It does not publish the bytes. A test asserts that no run reaches `publish-artifact`.

The [policy](policy.md) alone decides the ownership and the licensing of the source documents. This stage does not decide them.

## Testing

The transport is a `Protocol`. The tests use `FixtureTransport`. It serves canned responses and records each URL that the code requests. Thus a test can assert what the code did **not** fetch. An unregistered URL raises an error and does not return a 404. Thus a test that has no fixture fails loudly. It does not silently use the not-found path.

**The required CI contacts no third-party site.**
