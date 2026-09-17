# Technical debt

Debt is recorded here explicitly rather than hidden. Each entry states what is missing, why it was
acceptable to ship without it, and what would trigger paying it down.

## ~~TD-001~~ — retired

The full gauntlet landed with issue #5: property tests, Gherkin acceptance scenarios, coverage,
CRAP, mutation testing and a real-CLI smoke test, all wired into CI. See
[Quality gates](quality.md).

## TD-002 — `ty` is pre-1.0

**State.** Type checking uses Astral's `ty`, which is an alpha tool with an unstable configuration
surface and incomplete inference.

**Why.** It is the checker this project standardises on, it is fast, and it already catches real
errors. The alternative would be a second type checker in CI for no additional signal.

**Trigger.** Re-evaluate the pin when `ty` reaches a stable release, or if a false positive forces
a suppression that hides a genuine defect.

## TD-003 — branch protection does not include administrators

**State.** `main` requires `Deterministic quality gates`, `Docker smoke` and `Docs build` to pass,
requires branches to be up to date, forbids force pushes and deletions, and requires linear
history. It does **not** set `enforce_admins`, so a repository administrator can still merge past a
red gate.

**Why.** Locking administrators out of their own single-maintainer repository trades a real
recovery path for a theoretical guarantee.

**Trigger.** Enable `enforce_admins` if a second maintainer joins, or if a red gate is ever merged
past in practice.

## TD-004 — the pilot ships no curated allow-list entries

**State.** `ALLOWED_LICENSES` is populated, but nothing in the pipeline produces a
`curated-allowlist` declaration, so every artifact in the current run resolves to `metadata-only`.
The published dataset therefore contains provenance and hashes and **no third-party bytes**.

**Why.** Clearing a document for redistribution means a human establishing its terms. For an
arbitrary sample of the open web that is per-document work, and guessing is precisely what
[ADR-0005](adr/0005-conservative-publication-policy.md) refuses to do. Shipping the policy without
entries is the honest state, not a misconfiguration.

**Trigger.** Add entries — as reviewed, source-attributed records in this repository — when there
is a concrete set of documents whose terms have actually been established. Until then the dataset
card must say plainly that no source bytes are republished.

## ~~TD-005~~ — retired

The policy is consumed by the retrieval stage (every record carries a `decide()` verdict) and by
the publication stage, whose card is generated from `policy_summary()`. See
[Publishing](publishing.md).

## TD-005 (historical) — the policy was not wired into a pipeline stage

**State.** `domain.policy.decide()` is complete and tested, but nothing calls it: there is no
retrieval or extraction stage yet to produce artifacts for it to judge, and no publication stage to
consume its verdicts or to write `policy_summary()` into a dataset card.

**Why.** The policy is deliberately a standalone pure function, delivered ahead of the stages that
need it so that those stages are built against a decided rule rather than inventing one. Wiring it
into a stage that does not exist would mean writing that stage here.

**Trigger.** Issues #3 and #4 pass `require_artifact_hash=True` when judging retrieved bytes; issue
#2 writes the card from `policy_summary()` and publishes only what `decide()` permits. Until all
three land, the statement "this project republishes no third-party bytes" is true because no bytes
are published at all — not because the policy refused them.

## TD-006 — DNS rebinding and hostnames resolving to private addresses

**State.** `validate_url` refuses IP **literals** that name a loopback, private, link-local,
reserved, multicast or unspecified address. A *hostname* that resolves to one of those is not
caught, and neither is a host that resolves differently between the check and the request.

**Why.** Resolving a name is I/O, and the URL rules live in the pure domain precisely so they can
be tested without a network. Closing this properly means resolving in the adapter, checking the
resolved address, and pinning the connection to it — real work, and more than a bounded pilot that
fetches a few dozen public documents needs.

**Trigger.** Before this pipeline is ever pointed at untrusted URLs from inside a network with
anything worth reaching, or run as a service. Until then the exposure is a developer machine
fetching public PDFs.

## TD-007 — extracted image bytes are not portable across Pillow builds

**State.** Embedded images that are not already in a standard format are re-encoded to PNG by
Pillow. PNG encoding calls deflate, and the result depends on which implementation the installed
wheel was built against: this project's macOS wheel links **zlib-ng**, the Linux wheel in CI links
**plain zlib**, and they produce different bytes for identical pixels.

Consequently the same pipeline, on the same inputs, with the same pinned dependency versions,
produces **different image `sha256` values, different content-addressed paths and a different
`images_digest`** on a different platform. Everything else in the pipeline — selection, scoring,
retrieval manifests, PDF artifacts — is genuinely byte-identical; this stage is the exception.

**How it was found.** Golden tests pinning the encoded hashes passed locally and failed in CI.
That is the test doing its job.

**What is done about it.** The golden tests assert the **decoded pixels**, which are portable. Every
extract manifest records `encoder`: the pypdf version, the Pillow version and Pillow's zlib build,
so a published run says what produced it.

**Why it is not simply fixed.** The options all cost something: encoding at `compress_level=0`
removes the variance but inflates a 1241×1755 image from ~200 KB to ~6.5 MB; publishing the PDF's
original embedded stream bytes is faithful and portable but for raw-sample images is not a viewable
file; vendoring an encoder is disproportionate for a pilot.

**Trigger.** Before anyone relies on image hashes to compare two runs made on different machines,
or before the published dataset is regenerated on a different platform and the artifact paths
change. If that matters more than file size, publish the original embedded streams and record the
format per image.

## TD-008 — inline images are decoded before they can be counted

**State.** `max_images` is checked against the images a page *declares*, before this project decodes
any of them. That bounds image XObjects. It does not bound **inline** images — the `BI`/`ID`/`EI`
operators inside a content stream — because pypdf decodes each one in order to name it, inside the
same call that lists a page's images. A hand-built **1.4 KB** document carrying 300 flate-compressed
600×600 inline images peaks at roughly **300 MB** of resident memory before the limit fires. That is
a decompression bomb, and the input size bound from the retrieval stage does not help.

**What is done about it.** `max_pages` (default 300) bounds how many pages can do this, since each
page is parsed whether or not it contains images. The per-page exposure remains.

**Why it is not simply fixed.** Bounding a single page means not using pypdf's content-stream
parser — either pre-scanning the raw stream for inline-image operators before handing the page over,
or replacing the parser. Both are disproportionate for a pilot that fetches a few dozen public
documents under a 25 MB cap.

**How it was found.** An independent review measured it against the code that had just "fixed" the
XObject case. The regression test written at that time asserted only that *our* decode path did not
run, and could not observe decoding inside pypdf — it passed against the vulnerable code. That test
now says so in its own docstring.

**Trigger.** Before this stage is run over untrusted documents at scale, unattended, or anywhere a
300 MB spike per document matters. A per-process memory limit would be a cheaper mitigation than
replacing the parser.
