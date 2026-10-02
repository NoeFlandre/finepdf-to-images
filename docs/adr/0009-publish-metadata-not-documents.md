# ADR-0009: Publish an index, not a document dump

Status: accepted (2026-09-17)

## Context

The result of the pilot must go to a public place. Then it is a result. The obvious shape is "the PDFs and images that we found". This shape also republishes third-party documents. Nobody established the terms of these documents.

The publication policy ([ADR-0005](0005-conservative-publication-policy.md)) already answers this. Bytes need positive evidence that a person recorded. The pilot has none. Thus the question is not *if* we publish the documents. The question is what a useful dataset looks like when we cannot.

## Decision

Publish an **index**. The index has these rows:

- one row for each scored document, with its provenance, relevance evidence, retrieval outcome, and artifact hashes
- one row for each extracted image, with its dimensions and digest

The publication has four files in one commit. It has no source bytes.

Publish **each scored row**. This includes the irrelevant rows and the failed retrievals. Each row has its reason.

Generate the card from `policy_summary()` and `vocabulary_summary()`. Do not write it by hand.

Make the dry run structural. It never reaches the write path of the adapter.

Decide idempotency by content. Keep each timestamp out of the manifest.

## Consequences

- The dataset is useful for the purpose of a proof of concept. You can audit the filter, reproduce the selection, and judge if the approach is worth pursuing. It is not useful as a document corpus. This is the honest result of the policy. The second paragraph of the card says so. A reader does not have to find it.
- The publication of the failures makes the dataset larger and less flattering. It has 1000 rows. 52 are relevant and 16 are retrieved. A dataset that shows only the 16 misrepresents the pilot.
- The card cannot differ from the rules. But it is also harder to write prose in it. Anything interesting must be generated or be a literal in one template.
- The detection of a no-op depends on a content identity that the Hub exposes. The Hub gives a git blob id for an ordinary file. It gives a SHA-256 only for an LFS object. Thus the comparison accepts either. An earlier version compared SHA-256 alone. Then nothing matched. Each successful publication reported a verification failure and exited with 1. No second run was a no-op.
- The project does **not** implement byte publication. `build_plan` refuses a row that the policy cleared for it. It does not silently publish an index whose card claims otherwise. Thus to add allow-list entries is a change to this stage. It is not a configuration change. This is the honest position. An earlier draft of this ADR said the opposite.
