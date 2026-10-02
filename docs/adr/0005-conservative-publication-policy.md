# ADR-0005: Refuse by default, publish on positive evidence only

Status: accepted (2026-09-16)

## Context

The proof of concept publishes a public dataset. The dataset contains artifacts that the pipeline retrieved from arbitrary third-party web servers. FinePDFs has the ODC-BY license. This license covers the dataset: the extracted text, the metadata, and the compilation. It does not cover the copyright of the PDFs that the rows point to. A successful HTTP request is not a permission.

The tempting design is to publish what we retrieved and add a disclaimer. This puts the burden on the rights holders. They must find their work in the dataset of another person and ask for it back.

## Decision

Invert the default. `decide()` returns `metadata-only` unless both of these conditions are true:

- A `declared-open` status carries an allow-listed identifier.
- The status rests on `curated-allowlist` evidence. This is a human decision that is recorded in this repository.

Incomplete provenance returns `exclude` before the function checks the license.

`unknown` and `absent` are different statuses. The pipeline records response headers and URL heuristics as evidence. They are never sufficient.

## Consequences

- The pilot has no allow-list entries. Thus no artifact meets the condition for byte publication, even after the retrieval stage exists. The result is provenance and hashes only. This is a real and visible cost. It is the correct result of this policy on an arbitrary web sample. It does not show a wrong configuration. TD-004 records it.
- The policy exists before the stages that use it. Thus those stages use a rule that is already decided. They do not invent a rule. Until those stages exist, nothing calls `decide()`. TD-005 records this. Thus nobody can mistake "no bytes are published" for "the policy refused them".
- You can trace each published row to its exact FinePDFs row and source URL. This makes the metadata-only fallback useful. You can reproduce and verify the run from it.
- A decision to publish bytes requires an edit to this repository and a review. This is slow on purpose.
- The policy is one pure function. Thus a property test can prove the negative invariant, that no input path changes unknown to allowed. An inspection is not necessary.
- The project accepts takedown requests without proof of ownership. The cost to remove an item that we had no strong claim to publish is much lower than the cost of an error.
