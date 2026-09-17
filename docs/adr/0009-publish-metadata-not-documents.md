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
- No-op detection depends on the Hub exposing content hashes. It does so only for LFS entries, so
  small files re-upload rather than compare equal — safe, slightly wasteful, and documented.
- Anyone who later establishes terms for specific documents can add allow-list entries and the same
  command will publish their bytes. Nothing in this stage needs to change for that.
