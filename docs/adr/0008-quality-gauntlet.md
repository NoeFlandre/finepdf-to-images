# ADR-0008: What blocks a merge, and what does not

Status: accepted (2026-09-16)

## Context

A proof of concept with a published public dataset as its output needs evidence and not confidence. But a gate that a team can satisfy dishonestly is worse than no gate. It changes effort into a number. It does not change effort into safety.

## Decision

Nine gates block a merge: locked install, Ruff, `ty`, tests, property tests, acceptance scenarios, architecture checks, CRAP, and a smoke test of the real CLI and Docker. Mutation testing runs on each pull request. It **does not block**.

The CRAP threshold is 6. At full coverage, a function can have a complexity of 6. At 80% coverage, it can have about 3.

The acceptance scenarios are written in Gherkin. They run against the real stage functions. Only the shard reader and the HTTP transport are substituted.

Mutation testing covers the pure domain only.

## Consequences

- CRAP did real work. It did not decorate the README. To meet it, the team made the HTTP transport testable. Its client is now injectable. `httpx.MockTransport` tests the real streaming and redirect code offline. The team also split a dozen functions that had grown one branch at a time. The gate paid for itself.
- A team can also satisfy it dishonestly. It can cover a very complex function and not split it. The documentation says so. A reviewer must read the complexity column.
- Mutation testing is advisory because a kill rate starts a discussion. A surviving mutant sometimes needs a test. Sometimes the mutant is equivalent. A block on a percentage rewards assertions that copy the source. Instead, the team **classifies** the survivors. It kills the meaningful ones with tests that name them. It justifies the rest in writing.
- The smoke test is the only gate that runs the installed console script. Two real bugs passed a green suite and reached the repository. The smoke test caught them. One was a malformed `httpx.Timeout`. The other was a pypdf `DependencyError` that aborted a whole run. A fixture suite cannot fail in ways that its fixtures cannot express. This is the reason to keep this gate, although it is slow and noisy.
- The smoke job and the mutation job declare `needs: quality`. Thus the workflow enforces the documented order. A list in the documentation does not only imply it.
- To run all gates takes minutes and not seconds. For a repository of this size, this is affordable. If it becomes too expensive, make the gates faster. Do not make them fewer.
