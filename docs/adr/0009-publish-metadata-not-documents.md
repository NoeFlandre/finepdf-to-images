# ADR-0009 — Publish an index, not a document dump

Status: accepted (2026-09-17)

## Context

The pilot's result has to go somewhere public to be a result at all. The obvious shape is "the PDFs
and images we found", which is also the shape that republishes third-party documents whose terms
nobody established.

The publication policy ([ADR-0005](0005-conservative-publication-policy.md)) already answers that:
bytes require positive, human-recorded evidence, and the pilot ships none. So the question is not
*whether* to publish the documents but what a useful dataset looks like when you cannot.

## Decision

Publish an **index**: one row per scored document with its provenance, relevance evidence, retrieval
outcome and artifact hashes, plus one row per extracted image with its dimensions and digest. Four
files, one commit, no source bytes.

Publish **every scored row**, including the irrelevant ones and the failed retrievals, each with its
reason.

Generate the card from `policy_summary()` and `vocabulary_summary()` rather than writing it.

Make the dry run structural: it never reaches the adapter's write path.

Decide idempotency by content, and keep every timestamp out of the manifest.

## Consequences

- The dataset is useful for what a proof of concept is *for* — auditing the filter, reproducing the
  selection, judging whether the approach is worth pursuing — and useless as a document corpus.
  That is the honest result of the policy, and the card says so in its second paragraph rather than
  leaving a reader to discover it.
- Publishing the failures makes the dataset larger and less flattering: 1000 rows of which 52 are
  relevant and 16 retrieved. A dataset that showed only the 16 would misrepresent the pilot.
- The card cannot drift from the rules, but it is also harder to write prose in: anything
  interesting to say has to be either generated or a literal in one template.
- No-op detection depends on the Hub exposing a content identity. It gives a git blob id for an
  ordinary file and a SHA-256 only for an LFS object, so the comparison accepts either. An earlier
  version compared SHA-256 alone, which meant nothing ever matched: every successful publication
  would have reported a verification failure and exited 1, and no re-run would have been a no-op.
- Byte publication is **not** implemented, and `build_plan` refuses a row cleared for it rather
  than quietly publishing an index whose card claims otherwise. Adding allow-list entries is
  therefore a change to this stage, not a configuration change — which is the honest position, and
  the opposite of what an earlier draft of this ADR said.
