# ADR-0008 — What blocks a merge, and what does not

Status: accepted (2026-09-16)

## Context

A proof of concept whose output is a published public dataset needs evidence, not confidence. But a
gate that is easy to satisfy dishonestly is worse than no gate: it converts effort into a number
without converting it into safety.

## Decision

Eight gates block a merge: locked install, Ruff, `ty`, tests, property tests, acceptance scenarios,
architecture checks, CRAP, and a real-CLI plus Docker smoke test. Mutation testing runs on every
pull request and **does not block**.

CRAP's threshold is 6, chosen so that at full coverage a function may be as complex as 6 and at 80%
coverage about 3.

Acceptance scenarios are written in Gherkin and driven against the real stage functions, with only
the shard reader and HTTP transport substituted.

Mutation testing covers the pure domain only.

## Consequences

- CRAP did real work rather than decorating the README. Meeting it forced the HTTP transport to
  become testable — its client is now injectable and `httpx.MockTransport` exercises the genuine
  streaming and redirect code offline — and split a dozen functions that had grown a branch at a
  time. That is the gate paying for itself.
- It can also be satisfied dishonestly, by covering a monster rather than splitting it. The docs
  say so, and a reviewer is asked to read the complexity column.
- Mutation testing is advisory because a kill rate is a conversation. The honest answer to a
  surviving mutant is sometimes a test and sometimes "that one is equivalent"; blocking on a
  percentage rewards assertions that mirror the source. Instead the survivors are **classified**,
  the meaningful ones killed with tests that name them, and the rest justified in writing.
- The smoke test is the only gate that runs the installed console script. Two real bugs reached the
  repository past a green suite and were caught there — a malformed `httpx.Timeout` and a pypdf
  `DependencyError` that aborted a whole run. A fixture suite cannot fail in ways its fixtures
  cannot express, which is the argument for keeping this gate even though it is slow and noisy.
- Running everything takes minutes rather than seconds. For a repository this size that is
  affordable; if it stops being affordable, the answer is to make the gates faster, not fewer.
