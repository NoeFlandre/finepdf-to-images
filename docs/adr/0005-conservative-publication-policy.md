# ADR-0005 — Refuse by default, publish on positive evidence only

Status: accepted (2026-09-16)

## Context

The POC publishes a public dataset containing artifacts retrieved from arbitrary third-party web
servers. FinePDFs is ODC-BY, which licenses the dataset — extracted text, metadata, compilation —
and says nothing about the copyright in the PDFs its rows point at. A successful HTTP request is
not permission either.

The tempting design is to publish what we retrieved and add a disclaimer. That puts the burden on
rights holders to find their work in someone else's dataset and ask for it back.

## Decision

Invert the default. `decide()` returns `metadata-only` unless a `declared-open` status carries an
allow-listed identifier *and* rests on `curated-allowlist` evidence — a human decision recorded in
this repository. Incomplete provenance returns `exclude` before the licence is even consulted.

`unknown` and `absent` are distinct statuses. Response headers and URL heuristics are recorded as
evidence but are never sufficient.

## Consequences

- The pilot ships no allow-list entries, so the current run publishes **no third-party bytes at
  all** — only provenance and hashes. That is a real and visible cost, and it is the correct
  outcome of applying this policy to an arbitrary web sample rather than a sign the policy is
  misconfigured. Recorded as TD-004 so it is not mistaken for an oversight.
- Every published row is traceable to its exact FinePDFs row and source URL, which is what makes
  the metadata-only fallback genuinely useful: the run can be reproduced and verified from it.
- A decision to publish bytes requires editing this repository and passing review. That is slow on
  purpose.
- The policy is one pure function, so the negative invariant — no input path turns unknown into
  allowed — is provable by property test rather than by inspection.
- Takedown requests are honoured without requiring proof of ownership. Removing something we had no
  strong claim to publish is far cheaper than getting it wrong.
