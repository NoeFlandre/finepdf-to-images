# Provenance, licensing and publication policy

## The premise

**Being in FinePDFs is not permission to republish a document.**

FinePDFs is ODC-BY. That licence covers the *dataset* — the extracted text, the metadata, the
compilation. It says nothing about the copyright in the PDFs those rows point at, which belongs to
whoever published them. An HTTP 200 is not a licence either: it means a server sent bytes, not that
the bytes may be redistributed.

So the default answer for a retrieved artifact is **no**.

## The decision

One pure function, `finepdf_to_images.domain.policy.decide`, in this order:

1. **Incomplete provenance → `exclude`.** A row that cannot be traced back to its FinePDFs row and
   source URL is dropped entirely, not published as an untraceable metadata row. This check runs
   *first*: a perfect licence does not make an untraceable row publishable.
2. **Bytes → `publish-artifact`** only when all three hold together:
     - status is `declared-open`,
     - the identifier is on the allow list,
     - the evidence is `curated-allowlist`.
3. **Everything else → `metadata-only`.** Provenance and hashes, no bytes. Enough to reproduce and
   verify the run without redistributing the work.

There is no branch that promotes an unknown to an allowed licence, and no argument that can be set
to make one appear. That absence is the design. It is asserted by a Hypothesis property over the
full cross-product of status × evidence × identifier, not by a comment.

## Vocabulary

### Status — what is known

| | |
| --- | --- |
| `unknown` | Nothing was established. **The default, and by far the most common real answer.** |
| `absent` | The source states there are no terms. |
| `declared-open` | An open licence was positively identified. |
| `declared-restricted` | Terms exist and forbid redistribution. |
| `incompatible` | Terms exist but conflict with publishing here. |

`unknown` and `absent` are separate values on purpose. "We did not establish any terms" and "the
source says there are none" are different claims, and collapsing them is exactly how an unknown
quietly becomes a licence.

### Evidence — how well it is known

| | Trusted for bytes? |
| --- | --- |
| `none` | no |
| `url-heuristic` | no |
| `response-header` | no |
| `curated-allowlist` | **yes** |

Only a human decision recorded in this repository can support redistributing bytes. A licence
string in a response header is evidence of what a server said, not of what a rights holder permits.

### Allow list

`CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `PDM-1.0`, `public-domain`.

Short on purpose. Every entry is a commitment, and a longer list is not a better one. Matching is
exact: `cc-by-4.0` and `" CC-BY-4.0 "` do not match, so casing and whitespace cannot smuggle an
entry past it.

## Required provenance

`dataset`, `revision`, `config`, `split`, `shard`, `row_index`, `row_id`, `url`.

Every published row carries all of these, so any artifact can be traced back to the exact FinePDFs
row and source URL it came from. `row_index` of `0` is a real value, not an absence — a falsiness
check there would silently drop every shard's first row.

## Limitations

Source documents were published by third parties under terms this project does not control and
cannot verify at scale. Artifacts whose redistribution status could not be established are
represented by metadata and hashes only.

The pilot ships **no curated allow-list entries**, so in practice the current run publishes metadata
and hashes and no third-party bytes at all. That is the honest outcome of a conservative policy
applied to an arbitrary web sample, not a gap — see [technical debt](technical-debt.md) TD-004.

## Takedown

Open an issue at <https://github.com/NoeFlandre/finepdf-to-images/issues>, or use the Hugging Face
dataset's **Community** tab, to request removal of any artifact. Requests are honoured **without
requiring the requester to prove ownership**: the cost of removing something we had no strong claim
to publish is far lower than the cost of getting it wrong.

## Attribution

> Source documents were identified through
> [HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs), licensed ODC-BY.

This statement, the limitations and the takedown route are emitted into the published dataset card
by `policy_summary()`, so the card cannot drift from the code that enforces the policy.
