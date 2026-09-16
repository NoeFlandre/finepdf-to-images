# ADR-0006 — Decide in the domain, fetch in the adapter

Status: accepted (2026-09-16)

## Context

Retrieval is the one stage that sends requests to servers named in a web crawl. The interesting
decisions — may we request this URL, is this response really a PDF, is it too big — are exactly the
decisions that are hardest to test if they live next to a socket.

There is also a specific hazard: a URL out of a crawl can name the machine or network running the
pipeline. `http://169.254.169.254/latest/meta-data/` is a cloud metadata endpoint, not a document.

## Decision

Put every rule in `domain/retrieval.py` over plain values, and leave the adapter to move bytes.
`validate_url` raises before any network access. `evaluate` turns a completed response into a
success or a recorded failure. The transport is a `Protocol`, so tests drive fixtures.

Refuse non-http schemes, credentials in URLs, and any address that is loopback, private,
link-local, reserved, multicast or unspecified. Validate PDFs by magic bytes, never by content
type. Enforce the byte limit while streaming.

## Consequences

- The dangerous rules are readable and testable without a network, and required CI contacts no
  third-party site.
- A failure is a record, not an exception: the run can show *why* each URL produced nothing.
- Only IP **literals** can be checked in a pure function. A hostname that resolves to a private
  address still passes, because resolving it is I/O. Recorded as TD-006.
- The conservative URL rules will refuse some genuinely public documents — a host behind an
  unusual name, a URL with an odd shape. For a proof of concept that is the right direction to
  fail in, and every refusal is recorded with its reason rather than silently dropped.
