# ADR-0001: Pure domain, thin adapters

Status: accepted (2026-09-16)

## Context

The pipeline touches four difficult boundaries: Hugging Face, arbitrary third-party HTTP, the filesystem, and a PDF parser. All four are slow and sometimes nondeterministic. They are not available in the required CI. The decisions that we must review are none of these things. They are: Is this document about agriculture? Can we republish this artifact? What is the identity of this artifact?

## Decision

Split the package into a pure `domain` and `adapters` that have side effects. `cli` and `pipeline` are the only composition roots. Enforce the split with an AST-based architecture test. Do not use a style guide for this.

## Consequences

- You can test the domain behavior without a network, a token, or a fixture directory. This makes property tests and mutation testing affordable here.
- Each adapter has an explicit protocol. Thus tests can substitute fixtures. This is extra indirection in a very small codebase. We accept this price for a deterministic CI.
- A contributor who uses `pathlib` in `domain` gets a failing build. The contributor does not get a review comment three days later.
