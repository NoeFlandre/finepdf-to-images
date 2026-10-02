# ADR-0006: Decide in the domain, fetch in the adapter

Status: accepted (2026-09-16)

## Context

Retrieval is the only stage that sends requests to servers that a web crawl names. The important decisions are: Can we request this URL? Is this response really a PDF? Is it too big? These decisions are the hardest to test when they are next to a socket.

There is also a specific hazard. A URL from a crawl can name the machine or the network that runs the pipeline. `http://169.254.169.254/latest/meta-data/` is a cloud metadata endpoint. It is not a document.

## Decision

Put each rule in `domain/retrieval.py` over plain values. The adapter only moves bytes. `validate_url` raises an error before any network access. `evaluate` changes a completed response to a success or to a recorded failure. The transport is a `Protocol`. Thus tests use fixtures.

Refuse these items:

- non-http schemes
- credentials in URLs
- each address that is loopback, private, link-local, reserved, multicast, or unspecified

The last item includes the numeric encodings that `ipaddress` does not parse. For this reason a host must have a dot and a final label that starts with a letter. Validate PDFs by magic bytes. Never validate them by content type. Enforce the byte limit while the code streams.

Walk the redirects by hand and validate **each hop**. If you give the chain to the HTTP library, the rules apply to hop zero only. This is the same as no rules.

## Consequences

- You can read and test the dangerous rules without a network. The required CI contacts no third-party site.
- A failure is a record and not an exception. The run can show *why* each URL gave nothing.
- A pure function can check only IP **literals**. A host name that resolves to a private address still passes, because to resolve it is I/O. TD-006 records this.
- To drive redirects by hand needs more code than `follow_redirects=True`. It is the only version of this design that is actually true.
- The conservative URL rules refuse some public documents. Examples are a host with an unusual name and a URL with an unusual shape. For a proof of concept, this is the correct direction of failure. The pipeline records each refusal with its reason. It does not silently drop it.
