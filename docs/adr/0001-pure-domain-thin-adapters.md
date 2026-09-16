# ADR-0001 — Pure domain, thin adapters

Status: accepted (2026-09-16)

## Context

The pipeline touches four awkward boundaries: Hugging Face, arbitrary third-party HTTP, the
filesystem, and a PDF parser. All four are slow, occasionally nondeterministic, and unavailable in
required CI. The decisions we actually care about reviewing — is this document about agriculture,
may this artifact be republished, what is this artifact's identity — are none of those things.

## Decision

Split the package into a pure `domain` and side-effecting `adapters`, with `cli` and `pipeline` as
the only composition roots. Enforce the split with an AST-based architecture test rather than a
style guide.

## Consequences

- Domain behaviour is testable without a network, a token, or a fixture directory, which is what
  makes property tests and mutation testing affordable here.
- Adapters carry an explicit protocol so tests can substitute fixtures; that is extra indirection
  in a very small codebase, and it is the price we accept for a deterministic CI.
- A contributor who reaches for `pathlib` inside `domain` gets a failing build, not a review
  comment three days later.
